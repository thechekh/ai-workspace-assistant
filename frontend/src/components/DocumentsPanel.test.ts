import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useChatStore } from "../stores/chat";
import { confirmMock, fetchMock, requestUrl } from "../test/mocks";
import { FakeWebSocket } from "../test/fake-socket";
import DocumentsPanel from "./DocumentsPanel.vue";

let wrapper: VueWrapper | null = null;

async function mountOpenPanel() {
  const pinia = createPinia();
  setActivePinia(pinia);
  wrapper = mount(DocumentsPanel, { global: { plugins: [pinia] }, attachTo: document.body });
  const chat = useChatStore();
  chat.documents = [{ source: "guide.md", chunks: 2 }];
  await wrapper.find("button.ghost").trigger("click");
  await flushPromises();
  return { wrapper, chat };
}

function deleteCalls(): string[] {
  return fetchMock.mock.calls
    .filter(([, init]) => init?.method === "DELETE")
    .map(([url]) => requestUrl(url));
}

describe("DocumentsPanel", () => {
  beforeEach(() => {
    FakeWebSocket.reset();
    fetchMock.mockClear();
    confirmMock.mockReset();
  });

  afterEach(() => {
    wrapper?.unmount();
    wrapper = null;
  });

  it("opens the file picker from a real button, once", async () => {
    const { wrapper } = await mountOpenPanel();
    const input = wrapper.find<HTMLInputElement>("input[type=file]");
    const openPicker = vi.fn();
    input.element.click = openPicker;

    const choose = wrapper.find("button.choose-files");
    expect(choose.attributes("type")).toBe("button");
    await choose.trigger("click");
    // The button sits inside the clickable dropzone; the click must not
    // bubble into a second picker.
    expect(openPicker).toHaveBeenCalledTimes(1);

    await wrapper.find(".dropzone").trigger("click");
    expect(openPicker).toHaveBeenCalledTimes(2);
  });

  it("uploads what the picker returns", async () => {
    const { wrapper, chat } = await mountOpenPanel();
    const upload = vi.spyOn(chat, "uploadDocuments").mockResolvedValue();
    const input = wrapper.find<HTMLInputElement>("input[type=file]");
    const file = new File(["# hi"], "hi.md");
    Object.defineProperty(input.element, "files", { value: [file], configurable: true });

    await input.trigger("change");
    expect(upload).toHaveBeenCalledWith([file]);
  });

  it("asks before deleting a document", async () => {
    const { wrapper } = await mountOpenPanel();
    const remove = wrapper.find('button[aria-label="Remove guide.md"]');

    confirmMock.mockReturnValueOnce(false);
    await remove.trigger("click");
    await flushPromises();
    expect(deleteCalls()).toEqual([]);

    confirmMock.mockReturnValueOnce(true);
    await remove.trigger("click");
    await flushPromises();
    expect(deleteCalls()).toEqual(["/api/documents/guide.md"]);
    expect(confirmMock.mock.calls[1]?.[0]).toContain("guide.md");
  });
});
