import { createPinia, setActivePinia } from "pinia";
import { ref } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ServerEvent } from "../types";

/**
 * The store turns the server's WS frames into the chat transcript. Mock
 * useWebSocket so we can feed frames directly and assert the reducer, with no
 * socket and no server.
 */
let deliver: (data: string) => void = () => {};

vi.mock("@vueuse/core", () => ({
  useWebSocket: (_url: unknown, options: { onMessage: (ws: unknown, e: unknown) => void }) => {
    deliver = (data: string) => options.onMessage(null, { data });
    return { status: ref("OPEN"), send: vi.fn(), open: vi.fn(), close: vi.fn() };
  },
}));

// The store fetches /api/info and /api/health on creation.
vi.stubGlobal(
  "fetch",
  vi.fn(() => Promise.resolve({ ok: false, json: () => Promise.resolve({}) })),
);

import { useChatStore } from "./chat";

function send(event: ServerEvent): void {
  deliver(JSON.stringify(event));
}

describe("chat store — WS frame reducer", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    sessionStorage.clear();
  });

  it("accumulates token frames into one streaming message, then seals it on final", () => {
    const chat = useChatStore();
    send({ type: "token", content: "Hello" });
    send({ type: "token", content: " world" });

    expect(chat.items).toHaveLength(1);
    expect(chat.items[0]).toMatchObject({ kind: "assistant", text: "Hello world", streaming: true });

    send({ type: "final", content: "Hello world" });
    expect(chat.items).toHaveLength(1);
    expect(chat.items[0]).toMatchObject({ streaming: false, text: "Hello world" });
  });

  it("uses the final frame as authoritative if tokens were missed", () => {
    const chat = useChatStore();
    send({ type: "final", content: "complete answer" });
    expect(chat.items[0]).toMatchObject({ kind: "assistant", text: "complete answer" });
  });

  it("stores the session id from the session frame", () => {
    const chat = useChatStore();
    send({ type: "session", session_id: "abc123" });
    expect(chat.sessionId).toBe("abc123");
    expect(sessionStorage.getItem("session_id")).toBe("abc123");
  });

  it("pairs a tool_result with its pending tool_call card", () => {
    const chat = useChatStore();
    send({ type: "tool_call", tool: "search_docs", arguments: { query: "invoices" } });
    expect(chat.items[0]).toMatchObject({ kind: "tool", tool: "search_docs", result: null });

    send({ type: "tool_result", tool: "search_docs", result: "billing-service" });
    expect(chat.items[0]).toMatchObject({ result: "billing-service" });
  });

  it("attaches the turn frame's stats to the answer it describes", () => {
    const chat = useChatStore();
    send({ type: "final", content: "answer" });
    send({
      type: "turn",
      turn_id: "abc123def456",
      backend: "custom",
      duration_ms: 1200,
      first_token_ms: 900,
      llm_steps: 2,
      tool_calls: ["search_docs"],
      prompt_tokens: 100,
      completion_tokens: 20,
      usage_estimated: false,
      cost_usd: 0.0012,
    });

    expect(chat.items[0]).toMatchObject({
      kind: "assistant",
      stats: { turn_id: "abc123def456", cost_usd: 0.0012 },
    });
  });

  it("stops the streaming cursor and records an error frame", () => {
    const chat = useChatStore();
    send({ type: "token", content: "partial" });
    send({ type: "error", message: "LLM rate limit hit (429)" });

    expect(chat.items[0]).toMatchObject({ kind: "assistant", streaming: false });
    expect(chat.items[1]).toMatchObject({ kind: "error", text: "LLM rate limit hit (429)" });
  });

  it("starts a new assistant message for each turn", () => {
    const chat = useChatStore();
    send({ type: "token", content: "first" });
    send({ type: "final", content: "first" });
    send({ type: "token", content: "second" });
    send({ type: "final", content: "second" });

    expect(chat.items).toHaveLength(2);
    expect(chat.items[1]).toMatchObject({ text: "second" });
  });
});
