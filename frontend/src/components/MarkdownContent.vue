<script setup lang="ts">
import "highlight.js/styles/github-dark.css";

import { computed } from "vue";

import { renderMarkdown } from "../lib/markdown";

const props = defineProps<{ source: string }>();

const html = computed(() => renderMarkdown(props.source));
</script>

<template>
  <!-- markdown-it is configured with html: false, so model output is escaped -->
  <div class="markdown" v-html="html"></div>
</template>
