<script setup lang="ts">
import { ref } from "vue";

import { useChatStore } from "../stores/chat";

/**
 * Recent conversations. Sessions already survive a reload (Redis + the
 * `session_id` in the URL); this is what makes more than one of them
 * reachable — pick an old thread and the assistant carries on where it left
 * off, because the same history is what goes back into the prompt.
 */
const chat = useChatStore();
const open = ref(false);

function toggle(): void {
  open.value = !open.value;
  if (open.value) void chat.loadSessions();
}

async function pick(id: string): Promise<void> {
  await chat.switchSession(id);
  open.value = false;
}

function startNew(): void {
  chat.newSession();
  open.value = false;
}

/** Coarse on purpose: an exact clock adds noise to a "which one was it" list. */
function ago(updatedAt: number): string {
  const seconds = Math.max(0, Date.now() / 1000 - updatedAt);
  if (seconds < 90) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  return hours < 24 ? `${hours}h ago` : `${Math.round(hours / 24)}d ago`;
}
</script>

<template>
  <div class="sessions-wrap">
    <button class="ghost" title="Recent conversations" @click="toggle">
      Chats<span v-if="chat.sessions.length"> ({{ chat.sessions.length }})</span>
    </button>

    <div v-if="open" class="sessions-panel">
      <header class="sessions-head">
        <strong>Recent conversations</strong>
        <button class="link" @click="open = false">close</button>
      </header>

      <button class="new-chat" @click="startNew">+ New conversation</button>

      <p v-if="chat.sessionsLoading" class="muted">loading…</p>
      <p v-else-if="chat.sessions.length === 0" class="muted">
        No stored conversations yet. They appear here once you send a message, and expire with the
        session TTL.
      </p>

      <ul v-else class="session-list">
        <li
          v-for="session in chat.sessions"
          :key="session.session_id"
          :class="{ current: session.session_id === chat.sessionId }"
        >
          <button
            class="session-open"
            :title="session.session_id"
            @click="pick(session.session_id)"
          >
            <span class="session-preview">{{ session.preview || "(no messages)" }}</span>
            <span class="session-meta">
              {{ ago(session.updated_at) }} · {{ session.messages }} message{{
                session.messages === 1 ? "" : "s"
              }}
            </span>
          </button>
          <button
            class="link danger"
            title="Delete this conversation and its audit trail"
            @click="chat.deleteSession(session.session_id)"
          >
            delete
          </button>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.sessions-wrap {
  position: relative;
}
.sessions-panel {
  position: absolute;
  right: 0;
  top: calc(100% + 0.4rem);
  z-index: 20;
  width: 22rem;
  max-height: 26rem;
  overflow-y: auto;
  padding: 0.75rem;
  background: var(--panel, #fff);
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgb(0 0 0 / 12%);
}
.sessions-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.5rem;
}
.new-chat {
  width: 100%;
  margin-bottom: 0.6rem;
}
.session-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.session-list li {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.35rem 0;
  border-top: 1px solid var(--border);
}
.session-list li.current .session-preview {
  font-weight: 600;
}
.session-open {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.15rem;
  background: none;
  border: none;
  padding: 0.15rem 0;
  text-align: left;
  cursor: pointer;
  color: inherit;
}
.session-preview {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.session-meta,
.muted {
  font-size: 0.78rem;
  color: var(--muted, #6b7280);
}
.danger {
  color: var(--danger, #b3261e);
}
</style>
