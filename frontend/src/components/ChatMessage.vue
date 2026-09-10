<script setup lang="ts">
import { onBeforeUnmount, ref } from "vue";

import { copyToClipboard } from "../lib/clipboard";
import { useChatStore } from "../stores/chat";
import type { AssistantItem, AuditEvent, TurnEvent, UserItem } from "../types";
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

// Copies the answer as Markdown source — what you would paste into a doc.
const copied = ref<"ok" | "failed" | null>(null);
let copyReset: ReturnType<typeof setTimeout> | null = null;
async function copyAnswer(): Promise<void> {
  copied.value = (await copyToClipboard(props.item.text)) ? "ok" : "failed";
  if (copyReset !== null) clearTimeout(copyReset);
  copyReset = setTimeout(() => (copied.value = null), 1500);
}
onBeforeUnmount(() => {
  if (copyReset !== null) clearTimeout(copyReset);
});

/** Hover title: when the message appeared. Nothing for restored history. */
function receivedAt(at: number | undefined): string | undefined {
  return at === undefined ? undefined : `received ${new Date(at).toLocaleTimeString()}`;
}

function formatStats(stats: TurnEvent): string {
  const parts = [`${(stats.duration_ms / 1000).toFixed(1)}s`];
  if (stats.first_token_ms !== null) parts.push(`first token ${stats.first_token_ms} ms`);
  parts.push(`${stats.llm_steps} LLM step${stats.llm_steps === 1 ? "" : "s"}`);
  parts.push(
    `${stats.prompt_tokens}→${stats.completion_tokens} tok${stats.usage_estimated ? " (est)" : ""}`,
  );
  if (stats.cost_usd > 0) parts.push(`~$${stats.cost_usd.toFixed(4)}`);
  // Says why the numbers are what they are: both paths spend real tokens.
  if (stats.cancelled) parts.push("stopped");
  if (stats.failed) parts.push("failed");
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
      // Unreachable for the four types the server declares today, and kept
      // deliberately: these rows are parsed from JSON, so a server that
      // starts emitting a fifth kind should render a blank line, not crash
      // an already-finished turn's timeline.
      return "";
  }
}
</script>

<template>
  <div class="msg" :class="item.kind">
    <div class="avatar">{{ item.kind === "user" ? "You" : "AI" }}</div>
    <div class="bubble" :title="receivedAt(item.at)">
      <template v-if="item.kind === 'assistant'">
        <MarkdownContent :source="item.text" :streaming="item.streaming" />
        <span v-if="item.streaming" class="cursor">▍</span>
        <!-- Visible in both modes: an answer cut short must never read as a
             complete one, whether or not the dev stats line is showing. -->
        <div v-if="item.cancelled" class="stopped-note">
          <span aria-hidden="true">■</span>
          stopped by you{{ item.text ? "" : " before the answer started" }}
        </div>
        <div v-if="!item.streaming && item.text" class="bubble-actions">
          <button
            class="link"
            type="button"
            :aria-label="copied === 'ok' ? 'Copied' : 'Copy answer'"
            @click="copyAnswer"
          >
            {{ copied === "ok" ? "copied" : copied === "failed" ? "copy failed" : "copy" }}
          </button>
        </div>
        <div
          v-if="item.stats && chat.devMode"
          class="turn-stats"
          :title="`turn ${item.stats.turn_id} · backend ${item.stats.backend}`"
        >
          {{ formatStats(item.stats) }}
          <button class="link" type="button" @click="toggleDetails">
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

<style scoped>
.stopped-note {
  margin-top: 0.4rem;
  font-size: 0.78rem;
  color: var(--muted, #6b7280);
  display: flex;
  align-items: center;
  gap: 0.35rem;
}
</style>
