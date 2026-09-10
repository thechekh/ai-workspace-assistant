import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useChatStore } from "../stores/chat";
import { FakeWebSocket } from "../test/fake-socket";
import ChatWindow from "./ChatWindow.vue";

let wrapper: VueWrapper | null = null;

function mountWindow() {
  const pinia = createPinia();
  setActivePinia(pinia);
  wrapper = mount(ChatWindow, { global: { plugins: [pinia] }, attachTo: document.body });
  const chat = useChatStore();
  const socket = FakeWebSocket.last;
  socket.accept();
  return { wrapper, chat, socket };
}

/** One complete question/answer exchange through the real frame reducer. */
function exchange(
  chat: ReturnType<typeof useChatStore>,
  socket: FakeWebSocket,
  q: string,
  a: string,
) {
  chat.sendMessage(q);
  socket.deliver({ type: "final", content: a });
  socket.deliver({
    type: "turn",
    turn_id: "t",
    backend: "custom",
    duration_ms: 1,
    first_token_ms: null,
    llm_steps: 1,
    tool_calls: [],
    prompt_tokens: 1,
    completion_tokens: 1,
    usage_estimated: true,
    cost_usd: 0,
    cancelled: false,
    failed: false,
  });
}

/** Pretend the scroller has real geometry: happy-dom lays nothing out. */
function fakeGeometry(el: Element, scrollTop: number): void {
  Object.defineProperty(el, "scrollHeight", { value: 1000, configurable: true });
  Object.defineProperty(el, "clientHeight", { value: 300, configurable: true });
  Object.defineProperty(el, "scrollTop", { value: scrollTop, configurable: true, writable: true });
}

describe("ChatWindow", () => {
  beforeEach(() => {
    FakeWebSocket.reset();
    sessionStorage.clear();
    localStorage.clear();
  });

  afterEach(() => {
    wrapper?.unmount();
    wrapper = null;
  });

  it("keys items by id, so an earlier item leaving keeps later DOM nodes", async () => {
    const { wrapper, chat, socket } = mountWindow();
    exchange(chat, socket, "q1", "a1");
    exchange(chat, socket, "q2", "a2");
    await flushPromises();

    const bubbles = wrapper.findAll(".msg");
    expect(bubbles).toHaveLength(4);
    const answerOne = bubbles[1]?.element;
    expect(answerOne?.textContent).toContain("a1");

    chat.items.splice(0, 1); // the first question goes away
    await flushPromises();

    // With an index key this node would be recycled to show "q2".
    const first = wrapper.findAll(".msg")[0]?.element;
    expect(first).toBe(answerOne);
    expect(first?.textContent).toContain("a1");
  });

  it("follows the stream only while the reader is at the bottom", async () => {
    const { wrapper, chat, socket } = mountWindow();
    const scroller = wrapper.find("main.chat").element;
    const scrollTo = vi.fn();
    Object.defineProperty(scroller, "scrollTo", { value: scrollTo, configurable: true });

    chat.sendMessage("hello");
    socket.deliver({ type: "token", content: "Hi" });
    await flushPromises();
    expect(scrollTo).toHaveBeenCalled();
    expect(wrapper.find(".jump-latest").exists()).toBe(false);

    // The reader scrolls up to re-read something.
    fakeGeometry(scroller, 100);
    await wrapper.find("main.chat").trigger("scroll");
    expect(wrapper.find(".jump-latest").exists()).toBe(true);

    scrollTo.mockClear();
    socket.deliver({ type: "token", content: " there" });
    await flushPromises();
    expect(scrollTo).not.toHaveBeenCalled();

    // The pill takes them back and following resumes.
    await wrapper.find(".jump-latest").trigger("click");
    expect(scrollTo).toHaveBeenCalledTimes(1);
    expect(wrapper.find(".jump-latest").exists()).toBe(false);

    // Within 40px of the bottom counts as "at the bottom".
    fakeGeometry(scroller, 100);
    await wrapper.find("main.chat").trigger("scroll");
    expect(wrapper.find(".jump-latest").exists()).toBe(true);
    fakeGeometry(scroller, 670);
    await wrapper.find("main.chat").trigger("scroll");
    expect(wrapper.find(".jump-latest").exists()).toBe(false);
  });

  it("offers retry on the latest error row, resending the last question", async () => {
    const { wrapper, chat, socket } = mountWindow();
    chat.sendMessage("what is x?");
    socket.deliver({ type: "error", message: "boom" });
    await flushPromises();

    const retry = wrapper.find(".error button");
    expect(retry.text()).toBe("retry");
    await retry.trigger("click");

    const questions = socket.sent.filter((frame) => frame.includes("what is x?"));
    expect(questions).toHaveLength(2);
    // The error row is no longer the latest item, and a turn is running.
    expect(wrapper.find(".error button").exists()).toBe(false);
  });
});
