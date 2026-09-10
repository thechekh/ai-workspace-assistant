import { mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { FakeWebSocket } from "../test/fake-socket";
import { MAX_MESSAGE_CHARS } from "../types";
import ChatInput from "./ChatInput.vue";

let wrapper: VueWrapper | null = null;

function mountInput() {
  const pinia = createPinia();
  setActivePinia(pinia);
  wrapper = mount(ChatInput, { global: { plugins: [pinia] } });
  FakeWebSocket.last.accept();
  return wrapper;
}

describe("ChatInput", () => {
  beforeEach(() => {
    FakeWebSocket.reset();
    sessionStorage.clear();
    localStorage.clear();
  });

  afterEach(() => {
    wrapper?.unmount();
    wrapper = null;
  });

  it("caps the message at the server's limit and labels the field", () => {
    const textarea = mountInput().find("textarea");
    expect(textarea.attributes("maxlength")).toBe(String(MAX_MESSAGE_CHARS));
    expect(textarea.attributes("aria-label")).toBe("Message");
  });

  it("shows a counter only within 500 characters of the limit", async () => {
    const wrapper = mountInput();
    const textarea = wrapper.find("textarea");

    await textarea.setValue("a".repeat(100));
    expect(wrapper.find(".char-count").exists()).toBe(false);

    await textarea.setValue("a".repeat(MAX_MESSAGE_CHARS - 500));
    expect(wrapper.find(".char-count").text()).toBe(
      `${MAX_MESSAGE_CHARS - 500} / ${MAX_MESSAGE_CHARS}`,
    );
    expect(wrapper.find(".char-count").classes()).not.toContain("full");

    await textarea.setValue("a".repeat(MAX_MESSAGE_CHARS));
    expect(wrapper.find(".char-count").classes()).toContain("full");
  });

  it("sends on Enter and clears the draft", async () => {
    const wrapper = mountInput();
    const textarea = wrapper.find("textarea");
    await textarea.setValue("hello");
    await textarea.trigger("keydown", { key: "Enter" });

    expect(FakeWebSocket.last.sent).toEqual([
      JSON.stringify({ type: "user_message", content: "hello" }),
    ]);
    expect((textarea.element as HTMLTextAreaElement).value).toBe("");
  });
});
