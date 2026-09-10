import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import diff from "highlight.js/lib/languages/diff";
import dockerfile from "highlight.js/lib/languages/dockerfile";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import markdown from "highlight.js/lib/languages/markdown";
import python from "highlight.js/lib/languages/python";
import shell from "highlight.js/lib/languages/shell";
import sql from "highlight.js/lib/languages/sql";
import typescript from "highlight.js/lib/languages/typescript";
import yaml from "highlight.js/lib/languages/yaml";
import MarkdownIt from "markdown-it";

// Only the grammars an engineering assistant's answers and the docs corpus
// use. `lib/common` shipped ~40 of them, most dead weight in this bundle.
// Each grammar declares its own aliases (sh, js, ts, py, yml, md, docker…),
// so the fence tags models actually emit resolve without a mapping here.
const languages = {
  bash,
  diff,
  dockerfile,
  javascript,
  json,
  markdown,
  python,
  shell,
  sql,
  typescript,
  yaml,
};
for (const [name, grammar] of Object.entries(languages)) hljs.registerLanguage(name, grammar);

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

// Every fenced block gets a copy button. Injected as a render rule rather
// than by touching the DOM afterwards: the answer is re-rendered through
// v-html while it streams, which would drop anything added from outside.
// The click is handled by delegation in MarkdownContent.vue.
const renderFence =
  md.renderer.rules.fence ??
  ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options));
md.renderer.rules.fence = (tokens, idx, options, env, self) =>
  '<div class="code-block"><button type="button" class="copy-code" aria-label="Copy code">copy</button>' +
  renderFence(tokens, idx, options, env, self) +
  "</div>";

export function renderMarkdown(source: string): string {
  return md.render(source);
}
