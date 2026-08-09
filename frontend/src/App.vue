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
        <span class="health-dot" :class="chat.health?.status ?? 'unknown'" :title="healthTitle" />
        <span v-if="chat.info" class="badge" :title="`collection: ${chat.info.collection}`">
          {{ chat.info.llm_provider }} · {{ chat.info.retrieval_mode }}
        </span>
        <ModeToggle />
        <SessionsPanel />
        <DocumentsPanel />
        <BackendSelect />
        <span class="status" :class="chat.connected ? 'on' : 'off'">
          {{ chat.connected ? "connected" : "disconnected" }}
        </span>
        <button
          class="ghost"
          title="Re-ingest ASSISTANT_CORPUS_DIR (only if a corpus folder is configured)"
          @click="chat.reindex()"
        >
          Re-index
        </button>
      </div>
    </header>
    <ChatWindow />
    <ChatInput />
    <div class="toasts">
      <div v-for="toast in chat.toasts" :key="toast.id" class="toast" :class="toast.kind">
        {{ toast.text }}
      </div>
    </div>
  </div>
</template>
