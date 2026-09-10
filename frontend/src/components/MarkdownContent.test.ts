import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import { renderMarkdown } from "../lib/markdown";
import MarkdownContent from "./MarkdownContent.vue";

vi.mock("../lib/markdown", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/markdown")>();
  return { renderMarkdown: vi.fn(actual.renderMarkdown) };
});

const nextFrame = () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

describe("MarkdownContent", () => {
  it("parses at most once per frame while streaming, and at once on final", async () => {
    const wrapper = mount(MarkdownContent, { props: { source: "a", streaming: true } });
    vi.mocked(renderMarkdown).mockClear();

    for (let i = 2; i <= 20; i++) await wrapper.setProps({ source: "a".repeat(i) });
    expect(renderMarkdown).not.toHaveBeenCalled();

    await nextFrame();
    expect(renderMarkdown).toHaveBeenCalledTimes(1);
    expect(renderMarkdown).toHaveBeenLastCalledWith("a".repeat(20));

    await wrapper.setProps({ source: "**done**", streaming: false });
    expect(renderMarkdown).toHaveBeenCalledTimes(2);
    expect(wrapper.html()).toContain("<strong>done</strong>");
  });

  it("parses immediately when not streaming", async () => {
    const wrapper = mount(MarkdownContent, { props: { source: "one" } });
    await wrapper.setProps({ source: "*two*" });
    expect(wrapper.html()).toContain("<em>two</em>");
  });

  it("copies a code block from its button", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const wrapper = mount(MarkdownContent, { props: { source: "```python\nprint(1)\n```" } });

    await wrapper.find("button.copy-code").trigger("click");
    await flushPromises();
    expect(writeText).toHaveBeenCalledWith("print(1)\n");
    expect(wrapper.find("button.copy-code").text()).toBe("copied");
  });
});
