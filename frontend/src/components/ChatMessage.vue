<script setup lang="ts">
import { ref } from "vue";

import type { AssistantItem, UserItem } from "../stores/chat";
import { useChatStore } from "../stores/chat";
import type { AuditEvent, TurnEvent } from "../types";
import MarkdownContent from "./MarkdownContent.vue";

const props = defineProps<{ item: UserItem | AssistantItem }>();
const chat = useChatStore();

// "Explain this turn": lazily fetch the audit timeline on first expand.
const expanded = ref(false);
const events = ref<AuditEvent[] | null>(null);
const loading = ref(false);

async function toggleDetails(): Promise<void> {
  expanded.value = !expanded.value;
  if (!expanded.value || events.value !== null) return;
  if (props.item.kind !== "assistant" || !props.item.stats) return;
  loading.value = true;
  events.value = await chat.fetchTurnEvents(props.item.stats.turn_id);
  loading.value = false;
}

function formatStats(stats: TurnEvent): string {
  const parts = [`${(stats.duration_ms / 1000).toFixed(1)}s`];
  if (stats.first_token_ms !== null) parts.push(`first token ${stats.first_token_ms} ms`);
  parts.push(`${stats.llm_steps} LLM step${stats.llm_steps === 1 ? "" : "s"}`);
  parts.push(
    `${stats.prompt_tokens}→${stats.completion_tokens} tok${stats.usage_estimated ? " (est)" : ""}`,
  );
  if (stats.cost_usd > 0) parts.push(`~$${stats.cost_usd.toFixed(4)}`);
  if (stats.tool_calls.length > 0) parts.push(stats.tool_calls.join(", "));
  return parts.join(" · ");
}

function describeEvent(event: AuditEvent): string {
  switch (event.type) {
    case "tool_call":
      return `${event.tool ?? ""} ${event.arguments ?? ""}`.trim();
    case "tool_result":
      return `${event.tool ?? ""} → ${event.result_chars ?? 0} chars`;
    case "final":
      return `answer, ${event.chars ?? 0} chars`;
    case "error":
      return event.message ?? "";
    default:
      return "";
  }
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
          v-if="item.stats && chat.devMode"
          class="turn-stats"
          :title="`turn ${item.stats.turn_id} · backend ${item.stats.backend}`"
        >
          {{ formatStats(item.stats) }}
          <button class="link" @click="toggleDetails">
            {{ expanded ? "hide" : "details" }}
          </button>
        </div>
        <div v-if="expanded" class="turn-timeline">
          <template v-if="events && events.length > 0">
            <div v-for="(event, i) in events" :key="i" class="timeline-row">
              <span class="t-ms">+{{ event.ms }} ms</span>
              <span class="t-type" :class="event.type">{{ event.type }}</span>
              <span class="t-desc">{{ describeEvent(event) }}</span>
            </div>
          </template>
          <div v-else-if="loading" class="timeline-row muted">loading…</div>
          <div v-else class="timeline-row muted">no audit record for this turn</div>
        </div>
      </template>
      <span v-else>{{ item.text }}</span>
    </div>
  </div>
</template>
