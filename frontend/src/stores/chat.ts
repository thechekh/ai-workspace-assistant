import { useWebSocket } from "@vueuse/core";
import { defineStore } from "pinia";
import { computed, ref, watch } from "vue";

import type { ServerEvent, UserMessage } from "../types";

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

  async function reindex(): Promise<void> {
    const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
    try {
      const response = await fetch("/api/reindex", { method: "POST", headers });
      const payload = await response.json();
      if (response.ok) {
        toast("ok", payload.mode === "inline" ? `Re-indexed ${payload.chunks} chunks` : "Re-index queued");
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
          .find((i): i is ToolItem => i.kind === "tool" && i.tool === event.tool && i.result === null);
        if (pending) pending.result = event.result;
        break;
      }
      case "error":
        if (last && last.kind === "assistant" && last.streaming) last.streaming = false;
        items.value.push({ kind: "error", text: event.message });
        toast("error", event.message);
        break;
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

  function sendMessage(text: string): boolean {
    const trimmed = text.trim();
    if (!trimmed || !connected.value) return false;
    items.value.push({ kind: "user", text: trimmed });
    const payload: UserMessage = { type: "user_message", content: trimmed };
    send(JSON.stringify(payload));
    return true;
  }

  function newSession(): void {
    sessionStorage.removeItem("session_id");
    sessionId.value = null;
    items.value = [];
    close();
    open(); // wsUrl no longer carries session_id → server issues a fresh one
  }

  return {
    items,
    sessionId,
    backend,
    connected,
    status,
    info,
    toasts,
    sendMessage,
    newSession,
    reindex,
  };
});
