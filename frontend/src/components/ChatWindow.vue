<script setup lang="ts">
import { nextTick, ref, watch } from "vue";

import { useChatStore } from "../stores/chat";
import ChatMessage from "./ChatMessage.vue";
import ToolCard from "./ToolCard.vue";

const chat = useChatStore();
const scroller = ref<HTMLElement | null>(null);

watch(
  () => chat.items,
  async () => {
    await nextTick();
    scroller.value?.scrollTo({ top: scroller.value.scrollHeight });
  },
  { deep: true },
);
</script>

<template>
  <main ref="scroller" class="chat">
    <p v-if="chat.items.length === 0" class="empty">
      Ask about your documents, the codebase, or a URL.<br />
      Add documents with the <strong>Documents</strong> panel — the knowledge base starts empty.<br />
      Turn on <strong>Dev</strong> in the header to see tools, timings, tokens and cost.
    </p>
    <template v-for="(item, i) in chat.items" :key="i">
      <template v-if="item.kind === 'tool'">
        <!-- Dev mode shows the full tool card; standard mode shows only a
             quiet "working" hint while the call is still in flight, so the
             UI isn't silent during a slow tool. -->
        <ToolCard v-if="chat.devMode" :item="item" />
        <div v-else-if="item.result === null" class="tool-working">working…</div>
      </template>
      <div v-else-if="item.kind === 'error'" class="error">⚠ {{ item.text }}</div>
      <ChatMessage v-else :item="item" />
    </template>
  </main>
</template>
