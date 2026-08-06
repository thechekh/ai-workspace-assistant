<script setup lang="ts">
import { useChatStore } from "../stores/chat";

const chat = useChatStore();
</script>

<template>
  <label class="backend">
    agent:
    <select v-model="chat.backend">
      <option value="custom">custom loop</option>
      <option value="pydantic_ai">pydantic-ai</option>
      <option value="langgraph">langgraph</option>
    </select>
  </label>
</template>
