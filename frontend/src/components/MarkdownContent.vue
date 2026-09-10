<script setup lang="ts">
import "highlight.js/styles/github-dark.css";

import { nextTick, onBeforeUnmount, ref, watch } from "vue";

import { copyToClipboard } from "../lib/clipboard";
import { renderMarkdown } from "../lib/markdown";

const props = defineProps<{ source: string; streaming?: boolean }>();
// Parsing is deferred to an animation frame while streaming, so the bubble
// grows *after* the transcript said it changed. Without telling anyone, the
// chat window autoscrolls to a height the answer then exceeds — the last
// couple of lines and the stats line end up below the fold.
const emit = defineEmits<{ rendered: [] }>();

// markdown-it and highlight.js run over the whole answer on every parse, and
// tokens can arrive faster than frames are painted. While streaming, parses
// are coalesced to one per animation frame; the final text is parsed at once,
// so what stays on screen is never stale.
const html = ref(renderMarkdown(props.source));
let frame: number | null = null;

function render(): void {
  frame = null;
  html.value = renderMarkdown(props.source);
  void nextTick(() => emit("rendered"));
}

watch([() => props.source, () => props.streaming], ([, streaming]) => {
  if (streaming) {
    frame ??= requestAnimationFrame(render);
    return;
  }
  if (frame !== null) cancelAnimationFrame(frame);
  render();
});

// Label resets for the code-block copy buttons, cleared on unmount so a timer
// cannot fire against a node this component no longer owns.
const labelResets = new Set<ReturnType<typeof setTimeout>>();

onBeforeUnmount(() => {
  if (frame !== null) cancelAnimationFrame(frame);
  for (const timer of labelResets) clearTimeout(timer);
  labelResets.clear();
});

// One delegated handler for every code block's copy button: the buttons are
// part of the rendered HTML (see lib/markdown.ts) and replaced on each parse.
async function onClick(event: MouseEvent): Promise<void> {
  const target = event.target;
  if (!(target instanceof HTMLElement) || !target.classList.contains("copy-code")) return;
  const code = target.parentElement?.querySelector("pre code")?.textContent ?? "";
  target.textContent = (await copyToClipboard(code)) ? "copied" : "copy failed";
  const timer = setTimeout(() => {
    target.textContent = "copy";
    labelResets.delete(timer);
  }, 1500);
  labelResets.add(timer);
}
</script>

<template>
  <!-- markdown-it is configured with html: false, so model output is escaped -->
  <div class="markdown" @click="onClick" v-html="html"></div>
</template>
