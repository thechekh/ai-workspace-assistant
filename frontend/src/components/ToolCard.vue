<script setup lang="ts">
import type { ToolItem } from "../stores/chat";

defineProps<{ item: ToolItem }>();
</script>

<template>
  <div class="tool-card">
    <div class="tool-head">
      🔧 <strong>{{ item.tool }}</strong>
      <code v-if="item.args !== '{}'">{{ item.args }}</code>
    </div>
    <div v-if="item.result !== null" class="tool-result">{{ item.result }}</div>
    <div v-else class="tool-pending">running…</div>
  </div>
</template>
