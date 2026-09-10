import { createPinia, setActivePinia } from "pinia";
import { nextTick, ref, type Ref } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { requestUrl } from "../test/mocks";
import type { AuditTurn, ServerEvent, TurnEvent } from "../types";

/**
 * The store turns the server's WS frames into the chat transcript. Mock
 * useWebSocket so we can feed frames directly and assert the reducer, with no
 * socket and no server.
 */
let deliver: (data: string) => void = () => {};
/** What the store pushed onto the wire — asserted by the cancel tests. */
let sent: string[] = [];
const socketStatus = ref("OPEN");
/** What the store handed to useWebSocket: the URL it computes and its options. */
let socketUrl: () => string = () => "";
let socketOptions: SocketOptions | null = null;
const openSocket = vi.fn();
const closeSocket = vi.fn();

interface SocketOptions {
  autoConnect?: boolean;
  autoReconnect?: { retries?: number; delay?: number; onFailed?: () => void };
  onMessage: (ws: unknown, e: unknown) => void;
}

vi.mock("@vueuse/core", () => ({
  useWebSocket: (url: Ref<string>, options: SocketOptions) => {
    socketUrl = () => url.value;
    socketOptions = options;
    deliver = (data: string) => options.onMessage(null, { data });
    return {
      status: socketStatus,
      send: (data: string) => sent.push(data),
      open: openSocket,
      close: closeSocket,
    };
  },
}));

// The store fetches /api/info, /api/health and /api/documents on creation.
// Individual tests override this with `stubFetch` when they care about a
// specific endpoint.
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

function fetchCalls(): [string, RequestInit | undefined][] {
  return vi.mocked(fetch).mock.calls.map(([url, init]) => [requestUrl(url), init]);
}

import { TOAST_MS, useChatStore } from "./chat";

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
    failed: false,
    ...overrides,
  };
}

function reset(): void {
  setActivePinia(createPinia());
  sessionStorage.clear();
  localStorage.clear();
  history.replaceState(null, "", "/");
  sent = [];
  socketStatus.value = "OPEN";
  socketOptions = null;
  openSocket.mockClear();
  closeSocket.mockClear();
  vi.stubGlobal("fetch", vi.fn(notFound));
}

describe("chat store — WS frame reducer", () => {
  beforeEach(reset);

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

  it("pairs results with pending cards first-in first-out", () => {
    // One step may call the same tool twice; the results come back in call
    // order, so the older pending card is the one the first result belongs to.
    const chat = useChatStore();
    send({ type: "tool_call", tool: "search_docs", arguments: { query: "a" } });
    send({ type: "tool_call", tool: "search_docs", arguments: { query: "b" } });

    send({ type: "tool_result", tool: "search_docs", result: "result for a" });
    expect(chat.items[0]).toMatchObject({ args: '{"query":"a"}', result: "result for a" });
    expect(chat.items[1]).toMatchObject({ args: '{"query":"b"}', result: null });

    send({ type: "tool_result", tool: "search_docs", result: "result for b" });
    expect(chat.items[1]).toMatchObject({ result: "result for b" });
  });

  it("never pairs a result with a card left pending by an earlier turn", () => {
    const chat = useChatStore();
    chat.sendMessage("first");
    send({ type: "tool_call", tool: "search_docs", arguments: { query: "a" } });
    // Stopped mid-tool: that card's result is never coming.
    send(turnFrame({ cancelled: true, first_token_ms: null }));

    chat.sendMessage("second");
    send({ type: "tool_call", tool: "search_docs", arguments: { query: "b" } });
    send({ type: "tool_result", tool: "search_docs", result: "result for b" });

    const cards = chat.items.filter((item) => item.kind === "tool");
    expect(cards[0]).toMatchObject({ args: '{"query":"a"}', result: null });
    expect(cards[1]).toMatchObject({ args: '{"query":"b"}', result: "result for b" });
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

  it("announces the completed answer once, not token by token", () => {
    const chat = useChatStore();
    send({ type: "token", content: "Hel" });
    send({ type: "token", content: "lo" });
    expect(chat.announcement).toBe("");

    send({ type: "final", content: "Hello" });
    expect(chat.announcement).toBe("Hello");
  });
});

describe("chat store — transcript items", () => {
  beforeEach(reset);

  it("gives every item a stable, monotonic id and a timestamp", () => {
    const before = Date.now();
    const chat = useChatStore();
    chat.sendMessage("hello");
    send({ type: "tool_call", tool: "search_docs", arguments: {} });
    send({ type: "token", content: "hi" });
    send({ type: "error", message: "boom" });

    const ids = chat.items.map((item) => item.id);
    expect(ids).toEqual([1, 2, 3, 4]);
    for (const item of chat.items) {
      expect(item.at).toBeGreaterThanOrEqual(before);
      expect(item.at).toBeLessThanOrEqual(Date.now());
    }
  });

  it("keeps numbering restored history after the live items, without a timestamp", async () => {
    stubFetch({
      "/messages": {
        session_id: "s1",
        messages: [
          { role: "user", content: "q" },
          { role: "assistant", content: "a" },
        ],
      },
    });
    const chat = useChatStore();
    chat.sendMessage("live");
    await chat.switchSession("s1");

    expect(chat.items.map((item) => item.id)).toEqual([2, 3]);
    expect(chat.items.every((item) => item.at === undefined)).toBe(true);
  });
});

describe("chat store — standard vs dev mode", () => {
  beforeEach(reset);

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

describe("chat store — without web storage", () => {
  const realLocal = globalThis.localStorage;
  const realSession = globalThis.sessionStorage;

  it("starts and runs when storage access throws", () => {
    // Safari private mode, blocked site data: `window.localStorage` itself throws.
    const blocked = new Proxy(
      {},
      {
        get() {
          throw new DOMException("blocked", "SecurityError");
        },
      },
    );
    vi.stubGlobal("localStorage", blocked);
    vi.stubGlobal("sessionStorage", blocked);
    try {
      setActivePinia(createPinia());
      const chat = useChatStore();
      expect(chat.devMode).toBe(false);
      expect(() => chat.toggleDevMode()).not.toThrow();
      expect(chat.devMode).toBe(true);
      send({ type: "session", session_id: "abc" });
      expect(chat.sessionId).toBe("abc");
    } finally {
      vi.stubGlobal("localStorage", realLocal);
      vi.stubGlobal("sessionStorage", realSession);
    }
  });
});

describe("chat store — stopping a turn", () => {
  beforeEach(reset);

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
    expect(JSON.parse(sent[1] ?? "")).toEqual({ type: "cancel" });
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

  it("never borrows the previous turn's answer to mark a stop", () => {
    // Regression: the turn frame used to attach to "the last assistant message
    // anywhere", so stopping turn 2 before its first token relabelled turn 1's
    // finished reply as stopped and overwrote its stats.
    const chat = useChatStore();
    chat.sendMessage("first question");
    send({ type: "token", content: "the complete first answer" });
    send({ type: "final", content: "the complete first answer" });
    send(turnFrame({ turn_id: "turn-one" }));

    chat.sendMessage("second question");
    send(turnFrame({ turn_id: "turn-two", cancelled: true, first_token_ms: null }));

    const first = chat.items.find(
      (item) => item.kind === "assistant" && item.text === "the complete first answer",
    );
    expect(first).toBeTruthy();
    expect(first && "cancelled" in first ? first.cancelled : undefined).toBeFalsy();
    expect(first).toMatchObject({ stats: { turn_id: "turn-one" } });
    // The stopped turn gets its own (empty) bubble instead.
    expect(chat.items[chat.items.length - 1]).toMatchObject({
      kind: "assistant",
      text: "",
      cancelled: true,
    });
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

  it("keeps the cost of a failed turn on the partial answer", () => {
    // A turn that dies after the provider's retries has still spent tokens.
    // The summary arrives after the error frame and must not be dropped.
    const chat = useChatStore();
    chat.sendMessage("hello");
    send({ type: "token", content: "half an ans" });
    send({ type: "error", message: "the model produced an invalid tool call" });
    send(turnFrame({ failed: true, prompt_tokens: 3899, cost_usd: 0.0023 }));

    expect(chat.busy).toBe(false);
    const answer = chat.items.find((item) => item.kind === "assistant");
    expect(answer).toMatchObject({ stats: { failed: true, cost_usd: 0.0023 } });
    expect(chat.items.some((item) => item.kind === "error")).toBe(true);
  });

  it("retry re-asks the last question", () => {
    const chat = useChatStore();
    expect(chat.canRetry).toBe(false);
    expect(chat.retryLastMessage()).toBe(false);

    chat.sendMessage("what is x?");
    send({ type: "error", message: "boom" });
    expect(chat.canRetry).toBe(true);

    expect(chat.retryLastMessage()).toBe(true);
    expect(sent.filter((frame) => frame.includes("what is x?"))).toHaveLength(2);
    expect(chat.items.at(-1)).toMatchObject({ kind: "user", text: "what is x?" });
    expect(chat.canRetry).toBe(false); // a turn is running again
  });
});

describe("chat store — connection", () => {
  beforeEach(reset);

  it("keeps the documented reconnect policy and owns reconnects itself", () => {
    useChatStore();
    expect(socketOptions?.autoReconnect).toMatchObject({ retries: 10, delay: 2000 });
    // vueuse's URL watcher would reopen the socket mid session-switch.
    expect(socketOptions?.autoConnect).toBe(false);
  });

  it("toasts when auto-reconnect gives up, and reconnects on demand", () => {
    const chat = useChatStore();
    socketOptions?.autoReconnect?.onFailed?.();
    expect(chat.toasts.map((toast) => toast.text)).toEqual([
      expect.stringContaining("connection lost"),
    ]);

    chat.reconnect();
    expect(openSocket).toHaveBeenCalledTimes(1);
  });

  it("drops a toast after its time is up", () => {
    vi.useFakeTimers();
    try {
      const chat = useChatStore();
      send({ type: "error", message: "boom" });
      expect(chat.toasts).toHaveLength(1);

      vi.advanceTimersByTime(TOAST_MS - 1);
      expect(chat.toasts).toHaveLength(1);
      vi.advanceTimersByTime(1);
      expect(chat.toasts).toHaveLength(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops polling health once the store is disposed", () => {
    vi.useFakeTimers();
    try {
      const chat = useChatStore();
      const healthCalls = () => fetchCalls().filter(([url]) => url.includes("/api/health")).length;
      expect(healthCalls()).toBe(1);

      vi.advanceTimersByTime(10_000);
      expect(healthCalls()).toBe(2);

      chat.$dispose();
      vi.advanceTimersByTime(60_000);
      expect(healthCalls()).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("chat store — access token", () => {
  beforeEach(reset);

  it("captures ?token= into storage and strips it from the address bar", () => {
    history.replaceState(null, "", "/?token=s3cret&keep=1#top");
    const chat = useChatStore();

    expect(localStorage.getItem("assistant_token")).toBe("s3cret");
    expect(location.search).toBe("?keep=1");
    expect(location.hash).toBe("#top");
    expect(chat.hasToken).toBe(true);
    expect(socketUrl()).toContain("token=s3cret");
  });

  it("uses a stored token on later loads, as a bearer header", async () => {
    localStorage.setItem("assistant_token", "stored");
    stubFetch({ "/api/sessions": { sessions: [] } });
    const chat = useChatStore();
    await chat.loadSessions();

    expect(chat.hasToken).toBe(true);
    expect(fetchCalls()).toContainEqual([
      "/api/sessions",
      { headers: { Authorization: "Bearer stored" } },
    ]);
  });

  it("runs without a token", () => {
    const chat = useChatStore();
    expect(chat.hasToken).toBe(false);
    expect(socketUrl()).not.toContain("token=");
  });

  it("signs out by forgetting the token and reloading", () => {
    localStorage.setItem("assistant_token", "stored");
    const reload = vi.spyOn(location, "reload").mockImplementation(() => {});
    try {
      const chat = useChatStore();
      chat.signOut();
      expect(localStorage.getItem("assistant_token")).toBeNull();
      expect(reload).toHaveBeenCalledTimes(1);
    } finally {
      reload.mockRestore();
    }
  });
});

describe("chat store — agent backend", () => {
  beforeEach(reset);

  const info = {
    backends: ["custom", "langgraph"],
    default_backend: "langgraph",
    llm_provider: "fake",
    embedding_provider: "fake",
    retrieval_mode: "hybrid",
    collection: "docs",
    auth_required: false,
  };

  it("offers the built-in list until /api/info answers, then the server's", async () => {
    stubFetch({ "/api/info": info });
    const chat = useChatStore();
    expect(chat.backends).toEqual(["custom", "pydantic_ai", "langgraph"]);
    expect(chat.backend).toBe("custom");

    await vi.waitFor(() => expect(chat.info).not.toBeNull());
    expect(chat.backends).toEqual(["custom", "langgraph"]);
    expect(chat.backend).toBe("langgraph");
    // Not a choice: the socket leaves the pick to the server, which agrees.
    expect(socketUrl()).not.toContain("backend=");
    expect(openSocket).not.toHaveBeenCalled();
  });

  it("keeps a remembered choice over the server default", async () => {
    localStorage.setItem("assistant_backend", "custom");
    stubFetch({ "/api/info": info });
    const chat = useChatStore();
    await vi.waitFor(() => expect(chat.info).not.toBeNull());

    expect(chat.backend).toBe("custom");
    expect(socketUrl()).toContain("backend=custom");
  });

  it("drops a remembered choice this instance no longer offers", async () => {
    localStorage.setItem("assistant_backend", "pydantic_ai");
    stubFetch({ "/api/info": info });
    const chat = useChatStore();
    await vi.waitFor(() => expect(chat.info).not.toBeNull());

    expect(chat.backend).toBe("langgraph");
    expect(localStorage.getItem("assistant_backend")).toBeNull();
    expect(socketUrl()).not.toContain("backend=");
  });

  it("reconnects when the backend changes, and remembers the choice", async () => {
    const chat = useChatStore();
    chat.selectBackend("langgraph");
    await nextTick();

    expect(chat.backend).toBe("langgraph");
    expect(closeSocket).toHaveBeenCalledTimes(1);
    expect(openSocket).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem("assistant_backend")).toBe("langgraph");
    expect(socketUrl()).toContain("backend=langgraph");
  });
});

describe("chat store — audit trail", () => {
  beforeEach(reset);

  const turn: AuditTurn = {
    ...turnFrame(),
    events: [
      {
        ms: 12,
        type: "tool_call",
        tool: "search_docs",
        arguments: '{"query":"x"}',
        result_chars: null,
        chars: null,
        message: null,
      },
      {
        ms: 340,
        type: "final",
        tool: null,
        arguments: null,
        result_chars: null,
        chars: 80,
        message: null,
      },
    ],
  };

  it("fetches one turn's timeline for the current session", async () => {
    stubFetch({ "/turns/abc123def456": turn });
    const chat = useChatStore();
    send({ type: "session", session_id: "s1" });

    const events = await chat.fetchTurnEvents("abc123def456");
    expect(events).toEqual(turn.events);
    expect(fetchCalls()).toContainEqual(["/api/sessions/s1/turns/abc123def456", { headers: {} }]);
  });

  it("has nothing to fetch before a session exists", async () => {
    const chat = useChatStore();
    expect(await chat.fetchTurnEvents("abc123def456")).toBeNull();
    expect(fetchCalls().some(([url]) => url.includes("/turns/"))).toBe(false);
  });

  it("returns null for a turn the server does not know", async () => {
    const chat = useChatStore();
    send({ type: "session", session_id: "s1" });
    expect(await chat.fetchTurnEvents("nope")).toBeNull();
  });
});

describe("chat store — knowledge base", () => {
  beforeEach(reset);

  const ok = (payload: unknown) =>
    Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payload) });

  it("uploads files and pasted text in one request, then reloads the list", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        if (String(url) !== "/api/documents") return notFound();
        if (init?.method === "POST") {
          return ok({
            indexed: [
              { source: "a.md", chunks: 3 },
              { source: "notes.md", chunks: 1 },
            ],
            chunks: 4,
            skipped: ["b.exe (unsupported type)"],
          });
        }
        return ok({ documents: [{ source: "a.md", chunks: 3 }], total_chunks: 3 });
      }),
    );
    const chat = useChatStore();
    const file = new File(["# a"], "a.md", { type: "text/markdown" });
    await chat.uploadDocuments([file], { source: "notes.md", text: "hello" });

    const post = fetchCalls().find(([, init]) => init?.method === "POST");
    const body = post?.[1]?.body;
    expect(body).toBeInstanceOf(FormData);
    if (!(body instanceof FormData)) throw new Error("unreachable");
    expect(body.getAll("files")).toHaveLength(1);
    expect(body.get("text")).toBe("hello");
    expect(body.get("source")).toBe("notes.md");

    expect(chat.toasts.map((toast) => toast.text)).toEqual([
      "Indexed 4 chunks from 2 document(s)",
      "Skipped b.exe (unsupported type)",
    ]);
    expect(chat.documents).toEqual([{ source: "a.md", chunks: 3 }]);
    expect(chat.documentsLoading).toBe(false);
  });

  it("does nothing with neither files nor text", async () => {
    const chat = useChatStore();
    await chat.uploadDocuments([], { source: "x.md", text: "   " });
    expect(fetchCalls().some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("reports the server's reason when an upload is refused", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((_url: string, init?: RequestInit) =>
        init?.method === "POST"
          ? Promise.resolve({
              ok: false,
              status: 400,
              json: () => Promise.resolve({ detail: "no usable documents in the request" }),
            })
          : notFound(),
      ),
    );
    const chat = useChatStore();
    await chat.uploadDocuments([new File(["x"], "x.md")]);

    expect(chat.toasts.map((toast) => toast.text)).toEqual([
      "Upload failed: no usable documents in the request",
    ]);
    expect(chat.documentsLoading).toBe(false);
  });

  it("falls back to the status code when the error body is not JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((_url: string, init?: RequestInit) =>
        init?.method === "POST"
          ? Promise.resolve({
              ok: false,
              status: 502,
              json: () => Promise.reject(new Error("html")),
            })
          : notFound(),
      ),
    );
    const chat = useChatStore();
    await chat.uploadDocuments([new File(["x"], "x.md")]);
    expect(chat.toasts.map((toast) => toast.text)).toEqual(["Upload failed: HTTP 502"]);
  });

  it("deletes a document by path segment, keeping the slashes of repo sources", async () => {
    stubFetch({ "/api/documents": { documents: [], total_chunks: 0 } });
    const chat = useChatStore();
    await chat.deleteDocument("owner/repo/docs/a b.md");

    expect(fetchCalls()).toContainEqual([
      "/api/documents/owner/repo/docs/a%20b.md",
      { method: "DELETE", headers: {} },
    ]);
    expect(chat.toasts.map((toast) => toast.text)).toEqual(["Removed owner/repo/docs/a b.md"]);
  });

  it("reports a delete the server refused", async () => {
    const chat = useChatStore();
    await chat.deleteDocument("missing.md");
    expect(chat.toasts.map((toast) => toast.text)).toEqual(["Could not remove missing.md"]);
  });
});

describe("chat store — conversations", () => {
  beforeEach(reset);

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
    expect(chat.sessions[0]?.preview).toBe("how do I deploy?");
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

  it("closes the socket before fetching the history and reopens after", async () => {
    stubFetch({ "/messages": { session_id: "s1", messages: [] } });
    const chat = useChatStore();
    await chat.switchSession("s1");

    const fetchMock = vi.mocked(fetch);
    const historyIndex = fetchMock.mock.calls.findIndex(([url]) =>
      requestUrl(url).includes("/messages"),
    );
    const history = fetchMock.mock.invocationCallOrder[historyIndex] ?? Number.NaN;
    expect(closeSocket.mock.invocationCallOrder[0]).toBeLessThan(history);
    expect(openSocket.mock.invocationCallOrder[0]).toBeGreaterThan(history);
  });

  it("ignores a slow history fetch once a later switch has won", async () => {
    let resolveFirst: (value: unknown) => void = () => {};
    const first = new Promise((resolve) => (resolveFirst = resolve));
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (String(url).includes("/api/sessions/s1/messages")) return first;
        if (String(url).includes("/api/sessions/s2/messages")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () =>
              Promise.resolve({
                session_id: "s2",
                messages: [{ role: "user", content: "second" }],
              }),
          });
        }
        return notFound();
      }),
    );
    const chat = useChatStore();
    const slow = chat.switchSession("s1");
    const fast = chat.switchSession("s2");
    await fast;
    expect(chat.items).toHaveLength(1);
    expect(chat.items[0]).toMatchObject({ kind: "user", text: "second" });

    resolveFirst({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({ session_id: "s1", messages: [{ role: "user", content: "first" }] }),
    });
    await slow;

    expect(chat.sessionId).toBe("s2");
    expect(chat.items).toHaveLength(1);
    expect(chat.items[0]).toMatchObject({ kind: "user", text: "second" });
    // The loser must not reconnect on top of the winner either.
    expect(openSocket).toHaveBeenCalledTimes(1);
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
