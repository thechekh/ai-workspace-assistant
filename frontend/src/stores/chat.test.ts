import { createPinia, setActivePinia } from "pinia";
import { nextTick, ref } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ServerEvent, TurnEvent } from "../types";

/**
 * The store turns the server's WS frames into the chat transcript. Mock
 * useWebSocket so we can feed frames directly and assert the reducer, with no
 * socket and no server.
 */
let deliver: (data: string) => void = () => {};
/** What the store pushed onto the wire — asserted by the cancel tests. */
let sent: string[] = [];
const socketStatus = ref("OPEN");

vi.mock("@vueuse/core", () => ({
  useWebSocket: (_url: unknown, options: { onMessage: (ws: unknown, e: unknown) => void }) => {
    deliver = (data: string) => options.onMessage(null, { data });
    return {
      status: socketStatus,
      send: (data: string) => sent.push(data),
      open: vi.fn(),
      close: vi.fn(),
    };
  },
}));

// The store fetches /api/info and /api/health on creation. Individual tests
// override this with `stubFetch` when they care about a specific endpoint.
const notFound = () => Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) });
vi.stubGlobal("fetch", vi.fn(notFound));

/** Route fetches by URL fragment; anything unmatched 404s as before. */
function stubFetch(routes: Record<string, unknown>): void {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      const hit = Object.entries(routes).find(([fragment]) => String(url).includes(fragment));
      return hit
        ? Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(hit[1]) })
        : notFound();
    }),
  );
}

import { useChatStore } from "./chat";

function send(event: ServerEvent): void {
  deliver(JSON.stringify(event));
}

/** A representative `turn` frame; override only the field under test. */
function turnFrame(overrides: Partial<TurnEvent> = {}): TurnEvent {
  return {
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
    cancelled: false,
    ...overrides,
  };
}

describe("chat store — WS frame reducer", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    sessionStorage.clear();
    localStorage.clear();
    sent = [];
    socketStatus.value = "OPEN";
  });

  it("accumulates token frames into one streaming message, then seals it on final", () => {
    const chat = useChatStore();
    send({ type: "token", content: "Hello" });
    send({ type: "token", content: " world" });

    expect(chat.items).toHaveLength(1);
    expect(chat.items[0]).toMatchObject({
      kind: "assistant",
      text: "Hello world",
      streaming: true,
    });

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
    send(turnFrame());

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

describe("chat store — standard vs dev mode", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
  });

  it("defaults to standard mode", () => {
    expect(useChatStore().devMode).toBe(false);
  });

  it("toggles and persists the choice", () => {
    const chat = useChatStore();
    chat.toggleDevMode();
    expect(chat.devMode).toBe(true);
    expect(localStorage.getItem("assistant_dev_mode")).toBe("true");

    chat.toggleDevMode();
    expect(chat.devMode).toBe(false);
    expect(localStorage.getItem("assistant_dev_mode")).toBe("false");
  });

  it("restores dev mode from a previous session", () => {
    localStorage.setItem("assistant_dev_mode", "true");
    setActivePinia(createPinia());
    expect(useChatStore().devMode).toBe(true);
  });

  it("still records stats and tool calls in standard mode", () => {
    // The toggle is presentational only: hiding must not drop data, so
    // switching to dev reveals it retroactively.
    const chat = useChatStore();
    expect(chat.devMode).toBe(false);

    send({ type: "tool_call", tool: "search_docs", arguments: { query: "x" } });
    send({ type: "tool_result", tool: "search_docs", result: "found" });
    send({ type: "final", content: "answer" });
    send(turnFrame());

    expect(chat.items.find((i) => i.kind === "tool")).toMatchObject({ result: "found" });
    expect(chat.items.find((i) => i.kind === "assistant")).toMatchObject({
      stats: { cost_usd: 0.0012 },
    });
  });
});

describe("chat store — stopping a turn", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    sessionStorage.clear();
    localStorage.clear();
    sent = [];
    socketStatus.value = "OPEN";
  });

  it("is busy from send until the turn frame closes it", () => {
    const chat = useChatStore();
    expect(chat.busy).toBe(false);

    expect(chat.sendMessage("hello")).toBe(true);
    expect(chat.busy).toBe(true);

    send({ type: "final", content: "hi" });
    expect(chat.busy).toBe(true); // the summary is the real end of a turn

    send(turnFrame());
    expect(chat.busy).toBe(false);
  });

  it("refuses a second question while one is in flight", () => {
    const chat = useChatStore();
    chat.sendMessage("first");
    expect(chat.sendMessage("second")).toBe(false);
    expect(sent.filter((frame) => frame.includes("user_message"))).toHaveLength(1);
  });

  it("sends a cancel frame only when a turn is running", () => {
    const chat = useChatStore();
    chat.cancelTurn();
    expect(sent).toHaveLength(0);

    chat.sendMessage("hello");
    chat.cancelTurn();
    expect(JSON.parse(sent[1])).toEqual({ type: "cancel" });
  });

  it("marks the partial answer as stopped and keeps its text", () => {
    const chat = useChatStore();
    chat.sendMessage("hello");
    send({ type: "token", content: "partial ans" });
    send(turnFrame({ cancelled: true, completion_tokens: 3 }));

    expect(chat.items[1]).toMatchObject({
      kind: "assistant",
      text: "partial ans",
      streaming: false,
      cancelled: true,
    });
    expect(chat.busy).toBe(false);
  });

  it("still shows a stopped marker when no token arrived first", () => {
    const chat = useChatStore();
    chat.sendMessage("hello");
    send(turnFrame({ cancelled: true, first_token_ms: null }));

    expect(chat.items[1]).toMatchObject({ kind: "assistant", text: "", cancelled: true });
  });

  it("releases the composer when the socket drops mid-turn", async () => {
    const chat = useChatStore();
    chat.sendMessage("hello");
    send({ type: "token", content: "half an ans" });

    socketStatus.value = "CLOSED";
    await nextTick();

    expect(chat.busy).toBe(false);
    expect(chat.items[1]).toMatchObject({ kind: "assistant", streaming: false });
  });

  it("clears busy on an error frame so the composer is not wedged", () => {
    const chat = useChatStore();
    chat.sendMessage("hello");
    send({ type: "error", message: "LLM rate limit hit (429)" });
    expect(chat.busy).toBe(false);
  });
});

describe("chat store — conversations", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    sessionStorage.clear();
    localStorage.clear();
    sent = [];
    socketStatus.value = "OPEN";
  });

  it("loads the recent conversations list", async () => {
    stubFetch({
      "/api/sessions": {
        sessions: [
          { session_id: "s1", updated_at: 1_700_000_000, messages: 4, preview: "how do I deploy?" },
        ],
      },
    });
    const chat = useChatStore();
    await chat.loadSessions();

    expect(chat.sessions).toHaveLength(1);
    expect(chat.sessions[0].preview).toBe("how do I deploy?");
  });

  it("restores a transcript when reopening a conversation", async () => {
    stubFetch({
      "/messages": {
        session_id: "s1",
        messages: [
          { role: "user", content: "which service bills customers?" },
          { role: "assistant", content: "billing-service" },
          // Tool rows are prompt plumbing, not transcript.
          { role: "tool", content: "raw search output" },
        ],
      },
    });
    const chat = useChatStore();
    await chat.switchSession("s1");

    expect(chat.sessionId).toBe("s1");
    expect(sessionStorage.getItem("session_id")).toBe("s1");
    expect(chat.items).toHaveLength(2);
    expect(chat.items[0]).toMatchObject({ kind: "user", text: "which service bills customers?" });
    expect(chat.items[1]).toMatchObject({ kind: "assistant", streaming: false });
  });

  it("does not reload the conversation already open", async () => {
    stubFetch({ "/messages": { session_id: "s1", messages: [] } });
    const chat = useChatStore();
    await chat.switchSession("s1");
    chat.sendMessage("hello");

    await chat.switchSession("s1"); // same id: must be a no-op
    expect(chat.items.some((item) => item.kind === "user")).toBe(true);
  });

  it("clears a stuck busy flag when switching away mid-answer", async () => {
    stubFetch({ "/messages": { session_id: "s2", messages: [] } });
    const chat = useChatStore();
    chat.sendMessage("hello");
    expect(chat.busy).toBe(true);

    await chat.switchSession("s2");
    expect(chat.busy).toBe(false);
  });

  it("removes a deleted conversation from the list", async () => {
    stubFetch({
      "/api/sessions": {
        sessions: [
          { session_id: "s1", updated_at: 1, messages: 1, preview: "one" },
          { session_id: "s2", updated_at: 2, messages: 1, preview: "two" },
        ],
      },
    });
    const chat = useChatStore();
    await chat.loadSessions();

    await chat.deleteSession("s1");
    expect(chat.sessions.map((session) => session.session_id)).toEqual(["s2"]);
  });

  it("starts a fresh session when you delete the one you are in", async () => {
    stubFetch({ "/api/sessions": { sessions: [] } });
    const chat = useChatStore();
    await chat.switchSession("s1");
    chat.sendMessage("hello");

    await chat.deleteSession("s1");
    expect(chat.sessionId).toBeNull();
    expect(chat.items).toHaveLength(0);
    expect(chat.busy).toBe(false);
  });
});
