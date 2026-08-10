import { useWebSocket } from "@vueuse/core";
import { defineStore } from "pinia";
import { computed, ref, watch } from "vue";

import type {
  AuditEvent,
  AuditTurn,
  CancelRequest,
  IndexedDocument,
  ServerEvent,
  SessionSummary,
  StoredMessage,
  TurnEvent,
  UserMessage,
} from "../types";

export type BackendName = "custom" | "pydantic_ai" | "langgraph";

export interface PlatformInfo {
  backends: string[];
  default_backend: string;
  llm_provider: string;
  embedding_provider: string;
  retrieval_mode: string;
  collection: string;
  auth_required: boolean;
}

export interface Toast {
  id: number;
  kind: "ok" | "error";
  text: string;
}

export interface HealthComponent {
  status: string;
  [detail: string]: unknown;
}

export interface HealthInfo {
  status: "ok" | "degraded";
  components: Record<string, HealthComponent>;
}

/** Dev mode shows the instrumentation (tool cards, per-turn stats, the
 *  audit timeline); standard mode is a plain chat. Persisted across reloads.
 *  Nothing is filtered server-side — the frames always arrive, so flipping
 *  the switch reveals the data for messages already on screen. */
function resolveDevMode(): boolean {
  return localStorage.getItem("assistant_dev_mode") === "true";
}

/** Optional bearer token: captured once from ?token=... and persisted. */
function resolveToken(): string | null {
  const fromUrl = new URLSearchParams(location.search).get("token");
  if (fromUrl) localStorage.setItem("assistant_token", fromUrl);
  return localStorage.getItem("assistant_token");
}

export interface UserItem {
  kind: "user";
  text: string;
}
export interface AssistantItem {
  kind: "assistant";
  text: string;
  streaming: boolean;
  /** Answer cut short by Stop — rendered with a "stopped" marker. */
  cancelled?: boolean;
  /** Attached when the post-final `turn` frame arrives. */
  stats?: TurnEvent;
}
export interface ToolItem {
  kind: "tool";
  tool: string;
  args: string;
  result: string | null;
}
export interface ErrorItem {
  kind: "error";
  text: string;
}
export type ChatItem = UserItem | AssistantItem | ToolItem | ErrorItem;

export const useChatStore = defineStore("chat", () => {
  const items = ref<ChatItem[]>([]);
  const sessionId = ref<string | null>(sessionStorage.getItem("session_id"));
  const backend = ref<BackendName>("custom");
  const info = ref<PlatformInfo | null>(null);
  const toasts = ref<Toast[]>([]);
  const token = resolveToken();

  // True from "message sent" until the closing `turn` frame (or an error).
  // Drives the Stop button and blocks a second question mid-answer, which the
  // server would reject anyway.
  const busy = ref(false);

  const devMode = ref(resolveDevMode());
  function toggleDevMode(): void {
    devMode.value = !devMode.value;
    localStorage.setItem("assistant_dev_mode", String(devMode.value));
  }

  let toastSeq = 0;
  function toast(kind: Toast["kind"], text: string): void {
    const id = ++toastSeq;
    toasts.value.push({ id, kind, text });
    setTimeout(() => {
      toasts.value = toasts.value.filter((entry) => entry.id !== id);
    }, 4000);
  }

  async function loadInfo(): Promise<void> {
    try {
      const response = await fetch("/api/info");
      if (response.ok) info.value = (await response.json()) as PlatformInfo;
    } catch {
      /* offline dev server without backend — badge simply stays hidden */
    }
  }
  void loadInfo();

  // Deep health (/api/health pings Redis/Qdrant) -> header dot, refreshed
  // every 10s. Unreachable backend -> null -> gray "unknown" dot.
  const health = ref<HealthInfo | null>(null);
  async function loadHealth(): Promise<void> {
    try {
      const response = await fetch("/api/health");
      health.value = response.ok ? ((await response.json()) as HealthInfo) : null;
    } catch {
      health.value = null;
    }
  }
  void loadHealth();
  setInterval(() => void loadHealth(), 10_000);

  async function reindex(): Promise<void> {
    try {
      const response = await fetch("/api/reindex", { method: "POST", headers: authHeaders() });
      const payload = await response.json();
      if (response.ok) {
        toast(
          "ok",
          payload.mode === "inline" ? `Re-indexed ${payload.chunks} chunks` : "Re-index queued",
        );
      } else {
        toast("error", `Re-index failed: ${payload.detail ?? response.status}`);
      }
    } catch (error) {
      toast("error", `Re-index failed: ${String(error)}`);
    }
  }

  // Re-evaluated on every (re)connect: reconnects resume the same session,
  // and the backend switch rides along as a query param.
  const wsUrl = computed(() => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const params = new URLSearchParams({ backend: backend.value });
    if (sessionId.value) params.set("session_id", sessionId.value);
    if (token) params.set("token", token);
    return `${proto}://${location.host}/chat?${params.toString()}`;
  });

  /** The assistant bubble belonging to the turn now finishing, if it made one.
   *
   *  Scanning back for "the last assistant message" is wrong: a turn stopped
   *  before its first token produced no bubble, and would silently adopt the
   *  previous turn's answer — overwriting its stats and marking a finished
   *  reply as stopped. The user message that opened this turn is the boundary.
   */
  function answerOfCurrentTurn(): AssistantItem | undefined {
    for (let i = items.value.length - 1; i >= 0; i--) {
      const item = items.value[i];
      if (item.kind === "user") return undefined;
      if (item.kind === "assistant") return item;
    }
    return undefined;
  }

  function handleEvent(event: ServerEvent): void {
    const last = items.value[items.value.length - 1];
    switch (event.type) {
      case "session":
        sessionId.value = event.session_id;
        sessionStorage.setItem("session_id", event.session_id);
        break;
      case "token":
        if (last && last.kind === "assistant" && last.streaming) {
          last.text += event.content;
        } else {
          items.value.push({ kind: "assistant", text: event.content, streaming: true });
        }
        break;
      case "final":
        if (last && last.kind === "assistant" && last.streaming) {
          last.text = event.content;
          last.streaming = false;
        } else {
          items.value.push({ kind: "assistant", text: event.content, streaming: false });
        }
        break;
      case "tool_call":
        items.value.push({
          kind: "tool",
          tool: event.tool,
          args: JSON.stringify(event.arguments),
          result: null,
        });
        break;
      case "tool_result": {
        const pending = [...items.value]
          .reverse()
          .find(
            (i): i is ToolItem => i.kind === "tool" && i.tool === event.tool && i.result === null,
          );
        if (pending) pending.result = event.result;
        break;
      }
      case "error":
        if (last && last.kind === "assistant" && last.streaming) last.streaming = false;
        busy.value = false;
        items.value.push({ kind: "error", text: event.message });
        toast("error", event.message);
        break;
      case "turn": {
        // Always the last frame of a turn, on the stopped path too.
        busy.value = false;
        const answer = answerOfCurrentTurn();
        if (answer) {
          answer.stats = event;
          answer.streaming = false;
          if (event.cancelled) answer.cancelled = true;
        } else if (event.cancelled) {
          // Stopped before the first token: this turn has no bubble of its own,
          // and the previous turn's answer must not be borrowed to mark it.
          items.value.push({ kind: "assistant", text: "", streaming: false, cancelled: true });
        }
        // A failed turn already pushed an `error` item; the summary only adds
        // what it cost, which dev mode shows on the answer when there is one.
        break;
      }
    }
  }

  const { status, send, open, close } = useWebSocket(wsUrl, {
    autoReconnect: { retries: 10, delay: 2000 },
    onMessage(_ws, messageEvent) {
      handleEvent(JSON.parse(String(messageEvent.data)) as ServerEvent);
    },
  });

  const connected = computed(() => status.value === "OPEN");

  // Switching the agent backend reconnects with the same session — the new
  // runtime picks up the existing history from Redis.
  watch(backend, () => {
    close();
    open();
  });

  // A dropped socket takes its in-flight turn with it: no summary frame is
  // ever coming, so release the composer instead of wedging it on `busy`.
  watch(connected, (isConnected) => {
    if (!isConnected && busy.value) {
      busy.value = false;
      const last = items.value[items.value.length - 1];
      if (last && last.kind === "assistant" && last.streaming) last.streaming = false;
    }
  });

  function sendMessage(text: string): boolean {
    const trimmed = text.trim();
    if (!trimmed || !connected.value || busy.value) return false;
    items.value.push({ kind: "user", text: trimmed });
    const payload: UserMessage = { type: "user_message", content: trimmed };
    send(JSON.stringify(payload));
    busy.value = true;
    return true;
  }

  /** Stop the turn in flight. The server still sends its `turn` summary, so
   *  `busy` is cleared there rather than optimistically here. */
  function cancelTurn(): void {
    if (!busy.value || !connected.value) return;
    const payload: CancelRequest = { type: "cancel" };
    send(JSON.stringify(payload));
  }

  /** Audit timeline for one turn of the current session ("explain this turn"). */
  async function fetchTurnEvents(turnId: string): Promise<AuditEvent[] | null> {
    if (!sessionId.value) return null;
    try {
      // Ask for the single turn rather than downloading all 50 and filtering.
      const response = await fetch(`/api/sessions/${sessionId.value}/turns/${turnId}`, {
        headers: authHeaders(),
      });
      if (!response.ok) return null;
      const turn = (await response.json()) as AuditTurn;
      return turn.events ?? null;
    } catch {
      return null;
    }
  }

  // --- knowledge base ------------------------------------------------------
  // The index starts empty; documents are added here at runtime.
  const documents = ref<IndexedDocument[]>([]);
  const documentsLoading = ref(false);

  function authHeaders(): Record<string, string> {
    return token ? { Authorization: `Bearer ${token}` } : {};
  }

  async function loadDocuments(): Promise<void> {
    try {
      const response = await fetch("/api/documents");
      if (!response.ok) return;
      documents.value = ((await response.json()) as { documents: IndexedDocument[] }).documents;
    } catch {
      /* backend unreachable — the panel just shows nothing */
    }
  }

  async function uploadDocuments(files: File[], pasted?: { source: string; text: string }) {
    if (files.length === 0 && !pasted?.text.trim()) return;
    documentsLoading.value = true;
    const body = new FormData();
    for (const file of files) body.append("files", file);
    if (pasted?.text.trim()) {
      body.append("text", pasted.text);
      body.append("source", pasted.source || "pasted.md");
    }
    try {
      const response = await fetch("/api/documents", {
        method: "POST",
        headers: authHeaders(),
        body,
      });
      const payload = await response.json();
      if (response.ok) {
        toast("ok", `Indexed ${payload.chunks} chunks from ${payload.indexed.length} document(s)`);
        for (const skip of payload.skipped ?? []) toast("error", `Skipped ${skip}`);
        await loadDocuments();
      } else {
        toast("error", `Upload failed: ${payload.detail ?? response.status}`);
      }
    } catch (error) {
      toast("error", `Upload failed: ${String(error)}`);
    } finally {
      documentsLoading.value = false;
    }
  }

  async function deleteDocument(source: string): Promise<void> {
    try {
      const response = await fetch(`/api/documents/${encodeURIComponent(source)}`, {
        method: "DELETE",
        headers: authHeaders(),
      });
      if (response.ok) {
        toast("ok", `Removed ${source}`);
        await loadDocuments();
      } else {
        toast("error", `Could not remove ${source}`);
      }
    } catch (error) {
      toast("error", `Could not remove ${source}: ${String(error)}`);
    }
  }
  void loadDocuments();

  // --- conversations -------------------------------------------------------
  const sessions = ref<SessionSummary[]>([]);
  const sessionsLoading = ref(false);

  async function loadSessions(): Promise<void> {
    sessionsLoading.value = true;
    try {
      const response = await fetch("/api/sessions", { headers: authHeaders() });
      if (response.ok) {
        sessions.value = ((await response.json()) as { sessions: SessionSummary[] }).sessions;
      }
    } catch {
      /* offline: the panel simply shows nothing */
    } finally {
      sessionsLoading.value = false;
    }
  }

  /** Reopen a stored conversation: restore the transcript, then reconnect to it.
   *
   *  The WebSocket resumes history for the *model*, but never replays it, so
   *  the transcript is fetched over HTTP — otherwise reopening a conversation
   *  would show an empty window the assistant nonetheless remembered.
   */
  async function switchSession(id: string): Promise<void> {
    if (id === sessionId.value) return;
    sessionId.value = id;
    sessionStorage.setItem("session_id", id);
    items.value = [];
    busy.value = false;

    try {
      const response = await fetch(`/api/sessions/${id}/messages`, { headers: authHeaders() });
      if (response.ok) {
        const stored = ((await response.json()) as { messages: StoredMessage[] }).messages;
        items.value = stored
          .filter((message) => message.role === "user" || message.role === "assistant")
          .map((message) =>
            message.role === "user"
              ? ({ kind: "user", text: message.content } as ChatItem)
              : ({ kind: "assistant", text: message.content, streaming: false } as ChatItem),
          );
      }
    } catch {
      toast("error", "could not load that conversation's history");
    }

    close();
    open(); // reconnects with ?session_id=<id>
  }

  async function deleteSession(id: string): Promise<void> {
    try {
      const response = await fetch(`/api/sessions/${id}`, {
        method: "DELETE",
        headers: authHeaders(),
      });
      if (!response.ok) {
        toast("error", `could not delete that conversation (${response.status})`);
        return;
      }
      sessions.value = sessions.value.filter((item) => item.session_id !== id);
      // Deleting the conversation you are in leaves you somewhere real.
      if (id === sessionId.value) newSession();
    } catch (error) {
      toast("error", `could not delete that conversation: ${String(error)}`);
    }
  }

  function newSession(): void {
    sessionStorage.removeItem("session_id");
    sessionId.value = null;
    items.value = [];
    busy.value = false;
    close();
    open(); // wsUrl no longer carries session_id → server issues a fresh one
  }

  return {
    items,
    sessionId,
    backend,
    connected,
    busy,
    info,
    health,
    devMode,
    toggleDevMode,
    toasts,
    sendMessage,
    cancelTurn,
    newSession,
    sessions,
    sessionsLoading,
    loadSessions,
    switchSession,
    deleteSession,
    reindex,
    fetchTurnEvents,
    documents,
    documentsLoading,
    loadDocuments,
    uploadDocuments,
    deleteDocument,
  };
});
