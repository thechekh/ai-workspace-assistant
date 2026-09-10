import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FakeWebSocket } from "../test/fake-socket";
import type { AssistantItem } from "../types";
import ChatMessage from "./ChatMessage.vue";

function mountMessage(item: AssistantItem) {
  const pinia = createPinia();
  setActivePinia(pinia);
  return mount(ChatMessage, { props: { item }, global: { plugins: [pinia] } });
}

describe("ChatMessage", () => {
  beforeEach(() => {
    FakeWebSocket.reset();
  });

  it("copies the answer's Markdown and says when it arrived", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const at = Date.UTC(2026, 8, 6, 14, 3, 0);
    const wrapper = mountMessage({
      id: 1,
      kind: "assistant",
      text: "**hi**",
      streaming: false,
      at,
    });

    expect(wrapper.find(".bubble").attributes("title")).toBe(
      `received ${new Date(at).toLocaleTimeString()}`,
    );

    const copy = wrapper.find(".bubble-actions button");
    expect(copy.attributes("aria-label")).toBe("Copy answer");
    await copy.trigger("click");
    await flushPromises();
    expect(writeText).toHaveBeenCalledWith("**hi**");
    expect(copy.text()).toBe("copied");
  });

  it("has no timestamp for restored history and no copy button while streaming", () => {
    const restored = mountMessage({ id: 1, kind: "assistant", text: "old", streaming: false });
    expect(restored.find(".bubble").attributes("title")).toBeUndefined();

    const streaming = mountMessage({ id: 2, kind: "assistant", text: "par", streaming: true });
    expect(streaming.find(".bubble-actions").exists()).toBe(false);
  });
});
