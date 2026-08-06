<script setup lang="ts">
import BackendSelect from "./components/BackendSelect.vue";
import ChatInput from "./components/ChatInput.vue";
import ChatWindow from "./components/ChatWindow.vue";
import { useChatStore } from "./stores/chat";

const chat = useChatStore();
</script>

<template>
  <div class="app">
    <header>
      <div class="brand">
        <h1>AI Workspace Assistant</h1>
        <span class="phase">internal engineering assistant</span>
      </div>
      <div class="controls">
        <span v-if="chat.info" class="badge" :title="`collection: ${chat.info.collection}`">
          {{ chat.info.llm_provider }} · {{ chat.info.retrieval_mode }}
        </span>
        <BackendSelect />
        <span class="status" :class="chat.connected ? 'on' : 'off'">
          {{ chat.connected ? "connected" : "disconnected" }}
        </span>
        <button class="ghost" title="Re-ingest docs_corpus" @click="chat.reindex()">
          Re-index
        </button>
        <button class="ghost" @click="chat.newSession()">New session</button>
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
