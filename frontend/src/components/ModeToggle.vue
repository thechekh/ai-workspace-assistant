<script setup lang="ts">
import { useChatStore } from "../stores/chat";

/**
 * Standard vs Dev.
 *
 * Standard is a plain chat — just the conversation. Dev reveals the
 * instrumentation: tool cards, and the per-turn stats line (duration,
 * first-token latency, LLM steps, tokens, cost) with its expandable audit
 * timeline.
 *
 * The distinction is purely presentational. Every frame is still received and
 * stored, so switching is instant and retroactive: flip to Dev and the stats
 * appear on messages that are already on screen.
 */
const chat = useChatStore();
</script>

<template>
  <button
    class="mode-toggle"
    :class="{ on: chat.devMode }"
    role="switch"
    :aria-checked="chat.devMode"
    :title="
      chat.devMode
        ? 'Dev mode: showing tools, timings, tokens and cost. Click for a plain chat.'
        : 'Standard mode. Click to show tools, timings, tokens and cost.'
    "
    @click="chat.toggleDevMode()"
  >
    <span class="mode-dot" />
    {{ chat.devMode ? "Dev" : "Standard" }}
  </button>
</template>
