import { useWebSocket } from "@vueuse/core";
import { defineStore } from "pinia";
import { computed, onScopeDispose, ref, watch } from "vue";

import { readStorage, writeStorage } from "../lib/storage";
import type {
  AssistantItem,
  AuditEvent,
  AuditTurn,
  CancelRequest,
  ChatItem,
  ChatItemDraft,
  DocumentList,
  DocumentUploadResult,
  HealthInfo,
  IndexedDocument,
  PlatformInfo,
  ServerEvent,
  SessionList,
  SessionMessages,
  SessionSummary,
  StoredMessage,
  UserMessage,
} from "../types";

/** The runtime the server ships as its default. */
export const DEFAULT_BACKEND = "custom";
/** What the picker offers until /api/info says what this instance runs. */
export const FALLBACK_BACKENDS = [DEFAULT_BACKEND, "pydantic_ai", "langgraph"];

/** How long a toast stays on screen. */
export const TOAST_MS = 4000;
const HEALTH_INTERVAL_MS = 10_000;

const DEV_MODE_KEY = "assistant_dev_mode";
const TOKEN_KEY = "assistant_token";
const BACKEND_KEY = "assistant_backend";
const SESSION_KEY = "session_id";

export interface Toast {
  id: number;
  kind: "ok" | "error";
  text: string;
}

/** Dev mode shows the instrumentation (tool cards, per-turn stats, the
 *  audit timeline); standard mode is a plain chat. Persisted across reloads.
 *  Nothing is filtered server-side — the frames always arrive, so flipping
 *  the switch reveals the data for messages already on screen. */
function resolveDevMode(): boolean {
  return readStorage("local", DEV_MODE_KEY) === "true";
}

/** Optional bearer token: captured once from ?token=... and persisted.
 *
 *  The address bar is then rewritten without it. A secret left in the URL
 *  ends up in browser history, in screenshots and in any link copied from
 *  the bar. */
function resolveToken(): string | null {
  const params = new URLSearchParams(location.search);
  const fromUrl = params.get("token");
  if (fromUrl) {
    writeStorage("local", TOKEN_KEY, fromUrl);
    params.delete("token");
    const query = params.toString();
    const clean = `${location.pathname}${query ? `?${query}` : ""}${location.hash}`;
    history.replaceState(history.state, "", clean);
    return fromUrl;
  }
  return readStorage("local", TOKEN_KEY);
}

/** FastAPI's error body is `{detail}`; anything else (a proxy's HTML 502
 *  page, say) falls back to the status code. */
async function errorDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    /* not JSON */
  }
  return `HTTP ${response.status}`;
}

export const useChatStore = defineStore("chat", () => {
  const items = ref<ChatItem[]>([]);
  const sessionId = ref<string | null>(readStorage("session", SESSION_KEY));
  const info = ref<PlatformInfo | null>(null);
  const toasts = ref<Toast[]>([]);
  const token = resolveToken();
  const hasToken = computed(() => token !== null);
  /** The last completed answer, for the screen reader's live region:
   *  announced once and whole, rather than one token at a time. */
  const announcement = ref("");

  // True from "message sent" until the closing `turn` frame (or an error).
  // Drives the Stop button and blocks a second question mid-answer, which the
  // server would reject anyway.
  const busy = ref(false);

  const devMode = ref(resolveDevMode());
  function toggleDevMode(): void {
    devMode.value = !devMode.value;
    writeStorage("local", DEV_MODE_KEY, String(devMode.value));
  }

  let toastSeq = 0;
  function toast(kind: Toast["kind"], text: string): void {
    const id = ++toastSeq;
    toasts.value.push({ id, kind, text });
    setTimeout(() => {
      toasts.value = toasts.value.filter((entry) => entry.id !== id);
    }, TOAST_MS);
  }

  // Item ids are monotonic for the page's lifetime, so a v-for keyed on them
  // never hands one message's DOM (and component state) to another — which
  // an index key does whenever the transcript is replaced or trimmed.
  let itemSeq = 0;
  /** Stamp a draft with its key. `at: null` for restored history, whose
   *  original timing the server does not keep. */
  function createItem(draft: ChatItemDraft, at: number | null = Date.now()): ChatItem {
    return at === null ? { ...draft, id: ++itemSeq } : { ...draft, id: ++itemSeq, at };
  }

  // --- agent backend -------------------------------------------------------
  // The user's choice, remembered across reloads. null means "the server's
  // default": the socket then omits ?backend= and the server picks, and
  // /api/info tells the picker which one that is. Before it answers, the
  // hard-coded list stands in.
  const chosenBackend = ref<string | null>(readStorage("local", BACKEND_KEY));
  const backends = computed(() => info.value?.backends ?? FALLBACK_BACKENDS);
  /** What the socket is using: the choice, else the server's default. */
  const backend = computed(
    () => chosenBackend.value ?? info.value?.default_backend ?? DEFAULT_BACKEND,
  );
  function selectBackend(name: string | null): void {
    chosenBackend.value = name;
    writeStorage("local", BACKEND_KEY, name);
  }

  async function loadInfo(): Promise<void> {
    try {
      const response = await fetch("/api/info");
      if (!response.ok) return;
      info.value = (await response.json()) as PlatformInfo;
      // A remembered choice this instance no longer offers would leave the
      // picker blank, and the server ignores unknown names anyway.
      if (chosenBackend.value !== null && !info.value.backends.includes(chosenBackend.value)) {
        selectBackend(null);
      }
    } catch {
      /* offline dev server without backend — badge simply stays hidden */
    }
  }

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
  const healthTimer = setInterval(() => void loadHealth(), HEALTH_INTERVAL_MS);
  onScopeDispose(() => clearInterval(healthTimer));

  // Re-evaluated on every (re)connect: reconnects resume the same session,
  // and the backend switch rides along as a query param.
  const wsUrl = computed(() => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const params = new URLSearchParams();
    if (chosenBackend.value) params.set("backend", chosenBackend.value);
    if (sessionId.value) params.set("session_id", sessionId.value);
    if (token) params.set("token", token);
    return `${proto}://${location.host}/chat?${params.toString()}`;
  });

  /** Index of the first item of the turn in flight: everything after the
   *  user message that opened it. */
  function turnStart(): number {
    for (let i = items.value.length - 1; i >= 0; i--) {
      if (items.value[i]?.kind === "user") return i + 1;
    }
    return 0;
  }

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
      if (!item || item.kind === "user") return undefined;
      if (item.kind === "assistant") return item;
    }
    return undefined;
  }

  function handleEvent(event: ServerEvent): void {
    const last = items.value.at(-1);
    switch (event.type) {
      case "session":
        sessionId.value = event.session_id;
        writeStorage("session", SESSION_KEY, event.session_id);
        break;
      case "token":
        if (last?.kind === "assistant" && last.streaming) {
          last.text += event.content;
        } else {
          items.value.push(createItem({ kind: "assistant", text: event.content, streaming: true }));
        }
        break;
      case "final":
        if (last?.kind === "assistant" && last.streaming) {
          last.text = event.content;
          last.streaming = false;
        } else {
          items.value.push(
            createItem({ kind: "assistant", text: event.content, streaming: false }),
          );
        }
        announcement.value = event.content;
        break;
      case "tool_call":
        items.value.push(
          createItem({
            kind: "tool",
            tool: event.tool,
            // Empty stays empty rather than becoming "{}", so the card can ask
            // "are there arguments?" instead of comparing rendered JSON.
            args: Object.keys(event.arguments).length > 0 ? JSON.stringify(event.arguments) : "",
            result: null,
          }),
        );
        break;
      case "tool_result": {
        // Results come back in call order, so the first card still waiting
        // for this tool is the one — a step may call the same tool twice.
        // Bounded to the turn in flight: a card left pending by a stopped
        // turn must not swallow a later turn's result.
        for (let i = turnStart(); i < items.value.length; i++) {
          const item = items.value[i];
          if (item?.kind === "tool" && item.tool === event.tool && item.result === null) {
            item.result = event.result;
            break;
          }
        }
        break;
      }
      case "error":
        if (last?.kind === "assistant" && last.streaming) last.streaming = false;
        busy.value = false;
        items.value.push(createItem({ kind: "error", text: event.message }));
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
          items.value.push(
            createItem({ kind: "assistant", text: "", streaming: false, cancelled: true }),
          );
        }
        // A failed turn already pushed an `error` item; the summary only adds
        // what it cost, which dev mode shows on the answer when there is one.
        break;
      }
    }
  }

  const { status, send, open, close } = useWebSocket(wsUrl, {
    // Reconnects are explicit (backend switch, session switch, the header
    // pill). vueuse's own URL watcher would otherwise reopen the socket
    // whenever `wsUrl` changes: in the middle of a session switch while the
    // transcript is still loading, and after every `session` frame on a new
    // conversation — a second connection to the session just joined.
    autoConnect: false,
    autoReconnect: {
      retries: 10,
      delay: 2000,
      onFailed() {
        toast("error", "connection lost — click “disconnected” to retry");
      },
    },
    onMessage(_ws, messageEvent) {
      handleEvent(JSON.parse(String(messageEvent.data)) as ServerEvent);
    },
  });

  const connected = computed(() => status.value === "OPEN");

  /** Reconnect now — the "disconnected" pill, once auto-reconnect has given up. */
  function reconnect(): void {
    open();
  }

  // Switching the agent backend reconnects with the same session — the new
  // runtime picks up the existing history from Redis.
  watch(chosenBackend, () => {
    close();
    open();
  });

  // A dropped socket takes its in-flight turn with it: no summary frame is
  // ever coming, so release the composer instead of wedging it on `busy`.
  watch(connected, (isConnected) => {
    if (!isConnected && busy.value) {
      busy.value = false;
      const last = items.value.at(-1);
      if (last?.kind === "assistant" && last.streaming) last.streaming = false;
    }
  });

  function sendMessage(text: string): boolean {
    const trimmed = text.trim();
    if (!trimmed || !connected.value || busy.value) return false;
    items.value.push(createItem({ kind: "user", text: trimmed }));
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

  /** The most recent question, asked again — the error row's "retry". */
  function retryLastMessage(): boolean {
    for (let i = items.value.length - 1; i >= 0; i--) {
      const item = items.value[i];
      if (item?.kind === "user") return sendMessage(item.text);
    }
    return false;
  }
  const canRetry = computed(
    () => connected.value && !busy.value && items.value.some((item) => item.kind === "user"),
  );

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
      documents.value = ((await response.json()) as DocumentList).documents;
    } catch {
      /* backend unreachable — the panel just shows nothing */
    }
  }

  async function uploadDocuments(
    files: File[],
    pasted?: { source: string; text: string },
  ): Promise<void> {
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
      if (response.ok) {
        const result = (await response.json()) as DocumentUploadResult;
        toast("ok", `Indexed ${result.chunks} chunks from ${result.indexed.length} document(s)`);
        for (const skip of result.skipped) toast("error", `Skipped ${skip}`);
        await loadDocuments();
      } else {
        toast("error", `Upload failed: ${await errorDetail(response)}`);
      }
    } catch (error) {
      toast("error", `Upload failed: ${String(error)}`);
    } finally {
      documentsLoading.value = false;
    }
  }

  async function deleteDocument(source: string): Promise<void> {
    try {
      // Encode per segment: repo-ingested sources are "owner/repo/path" and the
      // slashes must survive as path separators for the {source:path} route.
      const encoded = source.split("/").map(encodeURIComponent).join("/");
      const response = await fetch(`/api/documents/${encoded}`, {
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

  // --- conversations -------------------------------------------------------
  const sessions = ref<SessionSummary[]>([]);
  const sessionsLoading = ref(false);

  async function loadSessions(): Promise<void> {
    sessionsLoading.value = true;
    try {
      const response = await fetch("/api/sessions", { headers: authHeaders() });
      if (response.ok) {
        sessions.value = ((await response.json()) as SessionList).sessions;
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
    writeStorage("session", SESSION_KEY, id);
    items.value = [];
    busy.value = false;
    // Closed before the fetch, not after: a frame from the old connection, or
    // a question sent while the history loads, would land in a transcript the
    // fetch is about to replace.
    close();

    let stored: StoredMessage[] | null = null;
    let failed = false;
    try {
      const response = await fetch(`/api/sessions/${id}/messages`, { headers: authHeaders() });
      if (response.ok) stored = ((await response.json()) as SessionMessages).messages;
    } catch {
      failed = true;
    }
    // A later switch won while this one was loading: what it fetched belongs
    // to a conversation no longer on screen, and the winner reconnects itself.
    if (sessionId.value !== id) return;

    if (stored) {
      items.value = stored
        .filter((message) => message.role === "user" || message.role === "assistant")
        .map((message) =>
          createItem(
            message.role === "user"
              ? { kind: "user", text: message.content }
              : { kind: "assistant", text: message.content, streaming: false },
            null,
          ),
        );
    } else if (failed) {
      toast("error", "could not load that conversation's history");
    }
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
    writeStorage("session", SESSION_KEY, null);
    sessionId.value = null;
    items.value = [];
    busy.value = false;
    close();
    open(); // wsUrl no longer carries session_id → server issues a fresh one
  }

  /** Forget the stored access token and start over without it. */
  function signOut(): void {
    writeStorage("local", TOKEN_KEY, null);
    location.reload();
  }

  void loadInfo();
  void loadHealth();
  void loadDocuments();

  return {
    items,
    sessionId,
    backend,
    backends,
    selectBackend,
    connected,
    reconnect,
    busy,
    info,
    health,
    hasToken,
    signOut,
    devMode,
    toggleDevMode,
    toasts,
    announcement,
    sendMessage,
    cancelTurn,
    retryLastMessage,
    canRetry,
    newSession,
    sessions,
    sessionsLoading,
    loadSessions,
    switchSession,
    deleteSession,
    fetchTurnEvents,
    documents,
    documentsLoading,
    loadDocuments,
    uploadDocuments,
    deleteDocument,
  };
});
