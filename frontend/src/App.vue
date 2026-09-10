<script setup lang="ts">
import { computed } from "vue";

import BackendSelect from "./components/BackendSelect.vue";
import ChatInput from "./components/ChatInput.vue";
import ChatWindow from "./components/ChatWindow.vue";
import DocumentsPanel from "./components/DocumentsPanel.vue";
import ModeToggle from "./components/ModeToggle.vue";
import SessionsPanel from "./components/SessionsPanel.vue";
import { useChatStore } from "./stores/chat";

const chat = useChatStore();

const healthTitle = computed(() => {
  if (!chat.health) return "health: unknown (backend unreachable)";
  const lines = Object.entries(chat.health.components).map(
    ([name, component]) => `${name}: ${component.status}`,
  );
  return [`health: ${chat.health.status}`, ...lines].join("\n");
});
</script>

<template>
  <div class="app">
    <header>
      <div class="brand">
        <h1>AI Workspace Assistant</h1>
        <span class="phase">internal engineering assistant</span>
      </div>
      <div class="controls">
        <span
          class="health-dot"
          :class="chat.health?.status ?? 'unknown'"
          role="img"
          :title="healthTitle"
          :aria-label="healthTitle"
        />
        <span v-if="chat.info" class="badge" :title="`collection: ${chat.info.collection}`">
          {{ chat.info.llm_provider }} · {{ chat.info.retrieval_mode }}
        </span>
        <ModeToggle />
        <SessionsPanel />
        <DocumentsPanel />
        <BackendSelect />
        <button
          v-if="chat.hasToken"
          class="ghost"
          type="button"
          title="Forget the stored access token and reload"
          @click="chat.signOut()"
        >
          sign out
        </button>
        <span v-if="chat.connected" class="status on">connected</span>
        <!-- Auto-reconnect gives up after ten tries; this is the manual eleventh. -->
        <button
          v-else
          class="status off"
          type="button"
          title="Reconnect now"
          @click="chat.reconnect()"
        >
          disconnected · retry
        </button>
      </div>
    </header>
    <ChatWindow />
    <ChatInput />
    <div class="toasts" role="status">
      <div v-for="toast in chat.toasts" :key="toast.id" class="toast" :class="toast.kind">
        {{ toast.text }}
      </div>
    </div>
    <!-- Screen readers hear each finished answer once, whole, rather than a
         stream of fragments as the tokens arrive. -->
    <div class="sr-only" aria-live="polite">{{ chat.announcement }}</div>
  </div>
</template>
