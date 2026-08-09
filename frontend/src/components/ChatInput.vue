<script setup lang="ts">
import { ref } from "vue";

import { useChatStore } from "../stores/chat";

const chat = useChatStore();
const draft = ref("");

function submit(): void {
  if (chat.sendMessage(draft.value)) draft.value = "";
}

function onKeydown(event: KeyboardEvent): void {
  // Escape stops the answer in flight — the keyboard twin of the Stop button.
  if (event.key === "Escape" && chat.busy) {
    event.preventDefault();
    chat.cancelTurn();
    return;
  }
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
      :placeholder="
        chat.busy
          ? 'Answering… (Esc or Stop to interrupt)'
          : 'Ask something… (Enter to send, Shift+Enter for a new line)'
      "
      @keydown="onKeydown"
    ></textarea>
    <!-- Send becomes Stop while a turn runs: one control, never both, so the
         button under the cursor always does the thing you can actually do. -->
    <button v-if="chat.busy" class="stop" title="Stop generating (Esc)" @click="chat.cancelTurn()">
      <span class="stop-glyph" aria-hidden="true"></span>
      Stop
    </button>
    <button v-else :disabled="!chat.connected || !draft.trim()" @click="submit">Send</button>
  </footer>
</template>

<style scoped>
.stop {
  background: var(--danger, #b3261e);
  border-color: var(--danger, #b3261e);
  color: #fff;
}
.stop-glyph {
  display: inline-block;
  width: 0.6em;
  height: 0.6em;
  margin-right: 0.45em;
  background: currentColor;
  border-radius: 1px;
  vertical-align: -0.02em;
}
</style>
