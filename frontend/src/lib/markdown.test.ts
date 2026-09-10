import { describe, expect, it } from "vitest";

import { renderMarkdown } from "./markdown";

/**
 * This renders *model-generated* text into v-html, so it is the app's main
 * XSS surface. markdown-it's `html: false` default is what protects us —
 * these tests fail loudly if anyone ever flips it on.
 */
describe("renderMarkdown — sanitization", () => {
  it("escapes raw HTML instead of emitting it", () => {
    const html = renderMarkdown("<script>alert('xss')</script>");
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
  });

  it("escapes inline event handlers in raw tags", () => {
    const html = renderMarkdown('<img src=x onerror="alert(1)">');
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
  });

  it("refuses to build a link from a javascript: URL", () => {
    // markdown-it leaves it as literal text rather than emitting an anchor —
    // what matters is that no href carries the scheme.
    const html = renderMarkdown("[click me](javascript:alert(1))");
    expect(html).not.toContain("<a ");
    expect(html).not.toMatch(/href\s*=\s*["']?javascript:/i);
  });

  it("refuses data: URLs in links too", () => {
    const html = renderMarkdown("[x](data:text/html;base64,PHNjcmlwdD4=)");
    expect(html).not.toMatch(/href\s*=\s*["']?data:/i);
  });

  it("escapes HTML nested inside a code fence", () => {
    const html = renderMarkdown("```\n<script>bad()</script>\n```");
    expect(html).not.toContain("<script>");
  });
});

describe("renderMarkdown — formatting", () => {
  it("renders headings, emphasis and lists", () => {
    expect(renderMarkdown("# Title")).toContain("<h1>");
    expect(renderMarkdown("**bold**")).toContain("<strong>");
    expect(renderMarkdown("- one\n- two")).toContain("<li>");
  });

  it("renders fenced code with a language class", () => {
    const html = renderMarkdown('```python\nprint("hi")\n```');
    expect(html).toContain("<pre>");
    expect(html).toContain("<code");
  });

  it("linkifies bare URLs (the assistant cites sources this way)", () => {
    const html = renderMarkdown("see https://example.com for details");
    expect(html).toContain('href="https://example.com"');
  });

  it("handles empty input without throwing", () => {
    expect(renderMarkdown("")).toBe("");
  });
});

describe("renderMarkdown — code blocks", () => {
  const fence = (lang: string, code: string) => renderMarkdown(`\`\`\`${lang}\n${code}\n\`\`\``);

  it("highlights the languages engineers paste", () => {
    const samples: [string, string][] = [
      ["python", "def f():\n    return 1"],
      ["typescript", "const x: number = 1;"],
      ["javascript", "const x = 1;"],
      ["bash", "echo hi"],
      ["shell", "$ ls"],
      ["json", '{"a": 1}'],
      ["yaml", "a: 1"],
      ["markdown", "# h"],
      ["sql", "SELECT 1"],
      ["diff", "+ added\n- removed"],
      ["dockerfile", "FROM python:3.12"],
    ];
    for (const [lang, code] of samples) {
      expect(fence(lang, code), lang).toContain("hljs-");
    }
  });

  it("resolves the short names models actually emit", () => {
    // Each sample contains something its grammar marks up; a bare `x = 1`
    // is unhighlighted in bash even though the alias resolves.
    const samples: [string, string][] = [
      ["py", "x = 1"],
      ["ts", "const x: number = 1;"],
      ["js", "const x = 1;"],
      ["sh", 'echo "hi"'],
      ["yml", "a: 1"],
      ["md", "# h"],
      ["docker", "FROM python:3.12"],
    ];
    for (const [alias, code] of samples) {
      expect(fence(alias, code), alias).toContain("hljs-");
    }
  });

  it("leaves a language it does not know as escaped plain text", () => {
    const html = fence("brainfuck", "<b>");
    expect(html).not.toContain("hljs-");
    expect(html).toContain("&lt;b&gt;");
  });

  it("gives every fenced block a copy button, and inline code none", () => {
    const html = renderMarkdown("```\na\n```\n\n```js\nb\n```");
    expect(html.match(/class="copy-code"/g)).toHaveLength(2);
    expect(html).toContain('aria-label="Copy code"');
    expect(renderMarkdown("use `x` here")).not.toContain("copy-code");
  });
});
