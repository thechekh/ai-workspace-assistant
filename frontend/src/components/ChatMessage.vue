<script setup lang="ts">
import type { AssistantItem, UserItem } from "../stores/chat";
import type { TurnEvent } from "../types";
import MarkdownContent from "./MarkdownContent.vue";

defineProps<{ item: UserItem | AssistantItem }>();

function formatStats(stats: TurnEvent): string {
  const parts = [`${(stats.duration_ms / 1000).toFixed(1)}s`];
  if (stats.first_token_ms !== null) parts.push(`first token ${stats.first_token_ms} ms`);
  parts.push(`${stats.llm_steps} LLM step${stats.llm_steps === 1 ? "" : "s"}`);
  parts.push(
    `${stats.prompt_tokens}→${stats.completion_tokens} tok${stats.usage_estimated ? " (est)" : ""}`,
  );
  if (stats.tool_calls.length > 0) parts.push(stats.tool_calls.join(", "));
  return parts.join(" · ");
}
</script>

<template>
  <div class="msg" :class="item.kind">
    <div class="avatar">{{ item.kind === "user" ? "You" : "AI" }}</div>
    <div class="bubble">
      <template v-if="item.kind === 'assistant'">
        <MarkdownContent :source="item.text" />
        <span v-if="item.streaming" class="cursor">▍</span>
        <div
          v-if="item.stats"
          class="turn-stats"
          :title="`turn ${item.stats.turn_id} · backend ${item.stats.backend}`"
        >
          {{ formatStats(item.stats) }}
        </div>
      </template>
      <span v-else>{{ item.text }}</span>
    </div>
  </div>
</template>
