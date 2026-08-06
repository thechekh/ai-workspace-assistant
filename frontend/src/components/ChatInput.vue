<script setup lang="ts">
import { ref } from "vue";

import { useChatStore } from "../stores/chat";

const chat = useChatStore();
const draft = ref("");

function submit(): void {
  if (chat.sendMessage(draft.value)) draft.value = "";
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    submit();
  }
}
</script>

<template>
  <footer class="composer">
    <textarea
      v-model="draft"
      rows="1"
      placeholder="Ask something… (Enter to send, Shift+Enter for a new line)"
      @keydown="onKeydown"
    ></textarea>
    <button :disabled="!chat.connected || !draft.trim()" @click="submit">Send</button>
  </footer>
</template>
