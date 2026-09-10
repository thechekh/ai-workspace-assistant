<script setup lang="ts">
import { useChatStore } from "../stores/chat";

const chat = useChatStore();

/** Human names for the runtimes we know; anything new from /api/info shows as is. */
const LABELS: Record<string, string> = {
  custom: "custom loop",
  pydantic_ai: "pydantic-ai",
  langgraph: "langgraph",
};

function label(name: string): string {
  return LABELS[name] ?? name;
}

function onChange(event: Event): void {
  chat.selectBackend((event.target as HTMLSelectElement).value);
}
</script>

<template>
  <label class="backend">
    agent:
    <select :value="chat.backend" @change="onChange">
      <option v-for="name in chat.backends" :key="name" :value="name">{{ label(name) }}</option>
    </select>
  </label>
</template>
