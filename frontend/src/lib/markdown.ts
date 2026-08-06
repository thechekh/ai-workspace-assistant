import hljs from "highlight.js/lib/common";
import MarkdownIt from "markdown-it";

// html: false (default) — model output is never injected as raw HTML.
const md = new MarkdownIt({
  linkify: true,
  highlight(code: string, lang: string): string {
    if (lang && hljs.getLanguage(lang)) {
      return hljs.highlight(code, { language: lang }).value;
    }
    return "";
  },
});

export function renderMarkdown(source: string): string {
  return md.render(source);
}
