<script setup lang="ts">
import { ref } from "vue";

import { usePopover } from "../lib/popover";
import { useChatStore } from "../stores/chat";

/**
 * The knowledge base starts empty — this is where you fill it. Drop or pick
 * .md/.txt/.rst files, or paste text, and the assistant can answer from them
 * on the very next message.
 */
const chat = useChatStore();
const wrap = ref<HTMLElement | null>(null);
const trigger = ref<HTMLElement | null>(null);
const { open, toggle, close } = usePopover("documents", wrap, trigger);
const dragging = ref(false);
const pastedText = ref("");
const pastedName = ref("");
const fileInput = ref<HTMLInputElement | null>(null);

function onToggle(): void {
  toggle();
  if (open.value) void chat.loadDocuments();
}

function pickFiles(): void {
  fileInput.value?.click();
}

async function onFiles(fileList: FileList | null): Promise<void> {
  if (!fileList || fileList.length === 0) return;
  await chat.uploadDocuments(Array.from(fileList));
  if (fileInput.value) fileInput.value.value = "";
}

async function onDrop(event: DragEvent): Promise<void> {
  dragging.value = false;
  await onFiles(event.dataTransfer?.files ?? null);
}

async function addPasted(): Promise<void> {
  if (!pastedText.value.trim()) return;
  await chat.uploadDocuments([], {
    source: pastedName.value || "pasted.md",
    text: pastedText.value,
  });
  pastedText.value = "";
  pastedName.value = "";
}

async function remove(source: string): Promise<void> {
  if (!window.confirm(`Remove “${source}” from the knowledge base?`)) return;
  await chat.deleteDocument(source);
}
</script>

<template>
  <div ref="wrap" class="docs-wrap">
    <button
      ref="trigger"
      class="ghost"
      type="button"
      title="Documents the assistant can search"
      :aria-expanded="open"
      aria-controls="documents-panel"
      @click="onToggle"
    >
      Documents<span v-if="chat.documents.length"> ({{ chat.documents.length }})</span>
    </button>

    <div v-if="open" id="documents-panel" class="docs-panel">
      <header class="docs-head">
        <strong>Knowledge base</strong>
        <button class="link" type="button" @click="close({ focusTrigger: true })">close</button>
      </header>

      <!-- Drag-and-drop for the mouse; the button for everyone else — a div
           with a click handler does not exist to the keyboard. -->
      <div
        class="dropzone"
        :class="{ over: dragging }"
        @dragover.prevent="dragging = true"
        @dragleave.prevent="dragging = false"
        @drop.prevent="onDrop"
        @click="pickFiles"
      >
        <input
          ref="fileInput"
          type="file"
          multiple
          accept=".md,.markdown,.txt,.rst"
          hidden
          @change="onFiles(($event.target as HTMLInputElement).files)"
        />
        <span v-if="chat.documentsLoading">indexing…</span>
        <span v-else>
          Drop .md / .txt / .rst here, or
          <button class="link choose-files" type="button" @click.stop="pickFiles">
            choose files
          </button>
        </span>
      </div>

      <details class="paste">
        <summary>or paste text</summary>
        <input v-model="pastedName" class="paste-name" placeholder="name.md" />
        <textarea v-model="pastedText" rows="4" placeholder="# Title&#10;Content…"></textarea>
        <button :disabled="!pastedText.trim() || chat.documentsLoading" @click="addPasted">
          Add
        </button>
      </details>

      <ul v-if="chat.documents.length" class="docs-list">
        <li v-for="doc in chat.documents" :key="doc.source">
          <span class="doc-name" :title="doc.source">{{ doc.source }}</span>
          <span class="doc-chunks">{{ doc.chunks }} chunks</span>
          <button
            class="link danger"
            type="button"
            :title="`Remove ${doc.source}`"
            :aria-label="`Remove ${doc.source}`"
            @click="remove(doc.source)"
          >
            ✕
          </button>
        </li>
      </ul>
      <p v-else class="docs-empty">
        Nothing indexed yet — the assistant will say so if you ask about internal docs.
      </p>
    </div>
  </div>
</template>
