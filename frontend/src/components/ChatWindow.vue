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
      Ask about the codebase, docs, or tools.<br />
      The default <code>fake</code> provider echoes offline — set
      <code>ASSISTANT_LLM_PROVIDER=groq</code> in <code>.env</code> for a real model.
    </p>
    <template v-for="(item, i) in chat.items" :key="i">
      <ToolCard v-if="item.kind === 'tool'" :item="item" />
      <div v-else-if="item.kind === 'error'" class="error">⚠ {{ item.text }}</div>
      <ChatMessage v-else :item="item" />
    </template>
  </main>
</template>
