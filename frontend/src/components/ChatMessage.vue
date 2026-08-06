<script setup lang="ts">
import type { AssistantItem, UserItem } from "../stores/chat";
import MarkdownContent from "./MarkdownContent.vue";

defineProps<{ item: UserItem | AssistantItem }>();
</script>

<template>
  <div class="msg" :class="item.kind">
    <div class="avatar">{{ item.kind === "user" ? "You" : "AI" }}</div>
    <div class="bubble">
      <template v-if="item.kind === 'assistant'">
        <MarkdownContent :source="item.text" />
        <span v-if="item.streaming" class="cursor">▍</span>
      </template>
      <span v-else>{{ item.text }}</span>
    </div>
  </div>
</template>
