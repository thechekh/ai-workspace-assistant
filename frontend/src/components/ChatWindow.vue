<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";

import { useChatStore } from "../stores/chat";
import ChatMessage from "./ChatMessage.vue";
import ToolCard from "./ToolCard.vue";

/** Within this many px of the bottom still counts as "reading the latest". */
const FOLLOW_THRESHOLD_PX = 40;

const chat = useChatStore();
const scroller = ref<HTMLElement | null>(null);
// Follow the stream only while the reader is at the bottom. Someone who has
// scrolled up to re-read an earlier answer must not be yanked back on every
// token; they get a pill to come back with instead.
const following = ref(true);

function onScroll(): void {
  const el = scroller.value;
  if (!el) return;
  following.value = el.scrollHeight - el.scrollTop - el.clientHeight <= FOLLOW_THRESHOLD_PX;
}

function scrollToBottom(): void {
  scroller.value?.scrollTo({ top: scroller.value.scrollHeight });
  following.value = true;
}

// What "the transcript grew" looks like without walking the transcript: how
// many items there are, which one is last, and how much text it is carrying.
// Reading exactly those properties subscribes to exactly them — a deep
// watcher re-walked every message on every streamed token, which is fine at
// ten messages and the first thing to slow down at a thousand.
const tail = computed(() => {
  const items = chat.items;
  const last = items.at(-1);
  if (!last) return "empty";
  const chars = last.kind === "tool" ? (last.result?.length ?? 0) : last.text.length;
  return `${items.length}:${last.id}:${chars}`;
});

/** Stay at the bottom when the content grew under us — never steal the view
 *  back from a reader who has scrolled up. */
function followIfAtBottom(): void {
  if (following.value) scrollToBottom();
}

watch(tail, async () => {
  await nextTick();
  followIfAtBottom();
});

// A different conversation always opens at its end.
watch(
  () => chat.sessionId,
  () => {
    following.value = true;
  },
);
</script>

<template>
  <main ref="scroller" class="chat" @scroll.passive="onScroll">
    <p v-if="chat.items.length === 0" class="empty">
      Ask about your documents, the codebase, or a URL.<br />
      Add documents with the <strong>Documents</strong> panel — the knowledge base starts empty.<br />
      Turn on <strong>Dev</strong> in the header to see tools, timings, tokens and cost.
    </p>
    <template v-for="(item, i) in chat.items" :key="item.id">
      <template v-if="item.kind === 'tool'">
        <!-- Dev mode shows the full tool card; standard mode shows only a
             quiet "working" hint while the call is still in flight, so the
             UI isn't silent during a slow tool. -->
        <ToolCard v-if="chat.devMode" :item="item" />
        <div v-else-if="item.result === null" class="tool-working">working…</div>
      </template>
      <div v-else-if="item.kind === 'error'" class="error">
        ⚠ {{ item.text }}
        <!-- Only on the latest error: retry re-asks the last question, which
             is what that row is about. -->
        <button
          v-if="i === chat.items.length - 1 && chat.canRetry"
          class="link"
          type="button"
          @click="chat.retryLastMessage()"
        >
          retry
        </button>
      </div>
      <ChatMessage v-else :item="item" @rendered="followIfAtBottom" />
    </template>
    <button v-if="!following" class="jump-latest" type="button" @click="scrollToBottom">
      ↓ jump to latest
    </button>
  </main>
</template>
