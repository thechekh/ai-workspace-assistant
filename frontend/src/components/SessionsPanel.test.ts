import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { defineComponent, h } from "vue";

import { useChatStore } from "../stores/chat";
import { confirmMock, fetchMock, requestUrl } from "../test/mocks";
import { FakeWebSocket } from "../test/fake-socket";
import DocumentsPanel from "./DocumentsPanel.vue";
import SessionsPanel from "./SessionsPanel.vue";

/** Both header popovers side by side, as App.vue mounts them. */
const Header = defineComponent({
  render: () => h("div", [h(SessionsPanel), h(DocumentsPanel)]),
});

let wrapper: VueWrapper | null = null;

function mountHeader() {
  const pinia = createPinia();
  setActivePinia(pinia);
  wrapper = mount(Header, { global: { plugins: [pinia] }, attachTo: document.body });
  const chat = useChatStore();
  return {
    wrapper,
    chat,
    chatsButton: wrapper.find(".sessions-wrap button.ghost"),
    docsButton: wrapper.find(".docs-wrap button.ghost"),
  };
}

describe("header popovers", () => {
  beforeEach(() => {
    FakeWebSocket.reset();
    fetchMock.mockClear();
    confirmMock.mockReset();
  });

  afterEach(() => {
    wrapper?.unmount();
    wrapper = null;
  });

  it("keeps only one popover open at a time", async () => {
    const { wrapper, chatsButton, docsButton } = mountHeader();
    await chatsButton.trigger("click");
    expect(wrapper.find("#sessions-panel").exists()).toBe(true);
    expect(chatsButton.attributes("aria-expanded")).toBe("true");

    await docsButton.trigger("click");
    expect(wrapper.find("#documents-panel").exists()).toBe(true);
    expect(wrapper.find("#sessions-panel").exists()).toBe(false);
    expect(chatsButton.attributes("aria-expanded")).toBe("false");
  });

  it("closes on Escape and returns focus to the trigger", async () => {
    const { wrapper, chatsButton } = mountHeader();
    await chatsButton.trigger("click");
    wrapper.find<HTMLElement>("#sessions-panel button.link").element.focus();

    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await flushPromises();
    expect(wrapper.find("#sessions-panel").exists()).toBe(false);
    expect(document.activeElement).toBe(chatsButton.element);
  });

  it("closes on a pointer press outside, but not inside", async () => {
    const { wrapper, chatsButton } = mountHeader();
    await chatsButton.trigger("click");

    wrapper
      .find("#sessions-panel")
      .element.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    await flushPromises();
    expect(wrapper.find("#sessions-panel").exists()).toBe(true);

    document.body.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    await flushPromises();
    expect(wrapper.find("#sessions-panel").exists()).toBe(false);
  });

  it("the close button hands focus back to the trigger", async () => {
    const { wrapper, chatsButton } = mountHeader();
    await chatsButton.trigger("click");
    await wrapper.find("#sessions-panel .sessions-head button.link").trigger("click");
    expect(wrapper.find("#sessions-panel").exists()).toBe(false);
    expect(document.activeElement).toBe(chatsButton.element);
  });

  it("asks before deleting a conversation", async () => {
    const { wrapper, chat, chatsButton } = mountHeader();
    chat.sessions = [{ session_id: "s1", updated_at: 1, messages: 2, preview: "how do I deploy?" }];
    await chatsButton.trigger("click");
    await flushPromises();

    const remove = wrapper.find('button[aria-label="Delete conversation: how do I deploy?"]');
    const deletes = () =>
      fetchMock.mock.calls
        .filter(([, init]) => init?.method === "DELETE")
        .map(([url]) => requestUrl(url));

    confirmMock.mockReturnValueOnce(false);
    await remove.trigger("click");
    await flushPromises();
    expect(deletes()).toEqual([]);
    expect(chat.sessions).toHaveLength(1);

    confirmMock.mockReturnValueOnce(true);
    await remove.trigger("click");
    await flushPromises();
    expect(deletes()).toEqual(["/api/sessions/s1"]);
  });
});
