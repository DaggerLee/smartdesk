<template>
  <div class="chat-window">
    <!-- Header toolbar -->
    <div class="chat-header">
      <div class="header-left">
        <span class="kb-icon">📚</span>
        <div>
          <div class="kb-title">{{ kb.name }}</div>
          <div class="kb-subtitle" v-if="kb.description">{{ kb.description }}</div>
        </div>
      </div>
      <div class="header-right">
        <FileUpload :kb-id="kb.id" @uploaded="onFileUploaded" />
        <button
          v-if="messages.length > 0"
          class="btn-clear"
          title="Clear conversation"
          @click="handleClear"
        >
          Clear Chat
        </button>
      </div>
    </div>

    <!-- Uploaded files bar -->
    <div v-if="uploadedFiles.length > 0" class="files-bar">
      <span class="files-bar-label">Files:</span>
      <div class="files-chips">
        <div v-for="f in uploadedFiles" :key="f.id" class="file-chip">
          <span class="file-chip-icon">📄</span>
          <span
            class="file-chip-name"
            :title="f.summary ? 'Click to view summary' : f.filename"
            :class="{ 'has-summary': f.summary }"
            @click="toggleSummary(f)"
          >{{ f.filename }}</span>
          <span class="file-chip-count">{{ f.chunk_count }} chunks</span>
          <button
            class="file-chip-delete"
            title="Delete file"
            @click="handleDeleteFile(f.filename)"
          >×</button>
        </div>
      </div>
    </div>

    <!-- File summary panel (shown when a file chip is clicked) -->
    <div v-if="activeSummaryFile" class="summary-panel">
      <div class="summary-panel-header">
        <span class="summary-panel-title">📄 {{ activeSummaryFile.filename }}</span>
        <button class="summary-panel-close" @click="activeSummaryFile = null">×</button>
      </div>
      <div class="summary-panel-body">
        <span v-if="activeSummaryFile.summary">{{ activeSummaryFile.summary }}</span>
        <span v-else class="summary-pending">Summary is being generated, please check back shortly…</span>
      </div>
    </div>

    <!-- Message area -->
    <div ref="messageArea" class="message-area">
      <!-- Empty state -->
      <div v-if="messages.length === 0 && !loading" class="empty-state">
        <div class="empty-icon">💬</div>
        <div class="empty-title">Start Chatting</div>
        <div class="empty-desc">
          Ask questions and get AI answers based on your uploaded documents.<br />
          Supports PDF and TXT files.
        </div>
      </div>

      <!-- Message list -->
      <template v-for="msg in messages" :key="msg.id">
        <!-- User message -->
        <div class="message user">
          <div class="bubble user-bubble">{{ msg.question }}</div>
          <div class="avatar user-avatar">Me</div>
        </div>
        <!-- AI response -->
        <div class="message ai">
          <div class="avatar ai-avatar">🤖</div>
          <div class="ai-content">
            <div class="bubble ai-bubble" v-html="renderMessageContent(msg)"></div>
            <!-- Sources -->
            <div v-if="msg.sources && msg.sources.length > 0" class="sources">
              <div class="sources-label">Sources</div>
              <div class="sources-list">
                <!-- Document source -->
                <div
                  v-for="(src, i) in msg.sources"
                  :key="i"
                  class="source-item"
                  :class="src.type === 'web' ? 'source-web' : 'source-doc'"
                >
                  <span class="source-icon">{{ src.type === 'web' ? '🌐' : '📄' }}</span>
                  <div class="source-body">
                    <!-- Document source layout -->
                    <template v-if="src.type !== 'web'">
                      <div class="source-filename">{{ src.filename }}</div>
                      <div class="source-preview">{{ src.preview }}…</div>
                    </template>
                    <!-- Web source layout -->
                    <template v-else>
                      <a
                        v-if="src.url"
                        class="source-title"
                        :href="src.url"
                        target="_blank"
                        rel="noopener noreferrer"
                      >{{ src.title }}</a>
                      <span v-else class="source-title-plain">{{ src.title }}</span>
                      <div v-if="src.url" class="source-url">{{ src.url }}</div>
                      <div v-if="src.snippet" class="source-preview">{{ src.snippet }}</div>
                    </template>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </template>
    </div>

    <!-- Input area -->
    <div class="input-area">
      <div class="input-box">
        <textarea
          v-model="inputText"
          placeholder="Type a question, Enter to send, Shift+Enter for new line…"
          rows="1"
          @keydown.enter.exact.prevent="handleSend"
          @input="autoResize"
          ref="textareaRef"
        ></textarea>
        <button class="btn-send" :disabled="sending || !inputText.trim()" @click="handleSend">
          {{ sending ? "Generating..." : "Send" }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { marked } from "marked";
import { nextTick, ref, watch } from "vue";
import { clearChatHistory, deleteFile, getChatHistory, listFiles, sendMessageStream } from "../api/index.js";
import FileUpload from "./FileUpload.vue";

const props = defineProps({
  kb: { type: Object, required: true },
});

const messages = ref([]);
const uploadedFiles = ref([]);
const inputText = ref("");
const sending = ref(false);
const loading = ref(false);
const messageArea = ref(null);
const textareaRef = ref(null);
const activeSummaryFile = ref(null);

// Reload history and file list whenever the active knowledge base changes
watch(
  () => props.kb.id,
  () => {
    loadHistory();
    loadFiles();
    activeSummaryFile.value = null;
  },
  { immediate: true }
);

async function loadHistory() {
  loading.value = true;
  try {
    const history = await getChatHistory(props.kb.id);
    messages.value = history;
    await nextTick();
    scrollToBottom();
  } catch (e) {
    console.error(e);
  } finally {
    loading.value = false;
  }
}

async function handleSend() {
  const text = inputText.value.trim();
  if (!text || sending.value) return;

  inputText.value = "";
  resetTextarea();
  sending.value = true;

  // Optimistically add the user message with an empty streaming answer
  const tempId = Date.now();
  messages.value.push({ id: tempId, question: text, answer: "", sources: [], streaming: true });
  await nextTick();
  scrollToBottom();

  try {
    await sendMessageStream(
      props.kb.id,
      text,
      (chunk) => {
        const idx = messages.value.findIndex((m) => m.id === tempId);
        if (idx !== -1) {
          messages.value[idx].answer += chunk;
          scrollToBottom();
        }
      },
      (sources) => {
        const idx = messages.value.findIndex((m) => m.id === tempId);
        if (idx !== -1) messages.value[idx].sources = sources;
      },
      () => {
        const idx = messages.value.findIndex((m) => m.id === tempId);
        if (idx !== -1) {
          messages.value[idx].answer = messages.value[idx].answer
            .replace("[SOURCE_USED]", "")
            .replace("[WEB_USED]", "")
            .trimEnd();
          messages.value[idx].streaming = false;
        }
      }
    );
  } catch (err) {
    const idx = messages.value.findIndex((m) => m.id === tempId);
    if (idx !== -1) {
      messages.value[idx].answer = `Request failed: ${err.message}`;
      messages.value[idx].streaming = false;
    }
  } finally {
    sending.value = false;
    await nextTick();
    scrollToBottom();
  }
}

async function handleClear() {
  if (!confirm("Clear all chat history for this knowledge base?")) return;
  await clearChatHistory(props.kb.id);
  messages.value = [];
}

async function loadFiles() {
  try {
    uploadedFiles.value = await listFiles(props.kb.id);
  } catch (e) {
    console.error(e);
  }
}

async function onFileUploaded() {
  await loadFiles();
}

async function handleDeleteFile(filename) {
  if (!confirm(`Delete "${filename}" and all its indexed content?`)) return;
  try {
    await deleteFile(props.kb.id, filename);
    if (activeSummaryFile.value?.filename === filename) activeSummaryFile.value = null;
    await loadFiles();
  } catch (err) {
    alert(`Delete failed: ${err.response?.data?.detail || err.message}`);
  }
}

function toggleSummary(file) {
  if (activeSummaryFile.value?.id === file.id) {
    activeSummaryFile.value = null;
  } else {
    activeSummaryFile.value = file;
  }
}

function renderMarkdown(text) {
  if (!text) return "";
  return marked.parse(text, { breaks: true });
}

function renderMessageContent(msg) {
  const html = renderMarkdown(msg.answer);
  if (msg.streaming) return html + '<span class="streaming-cursor">▋</span>';
  return html;
}

function scrollToBottom() {
  if (messageArea.value) {
    messageArea.value.scrollTop = messageArea.value.scrollHeight;
  }
}

function autoResize(e) {
  const el = e.target;
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 120) + "px";
  el.style.overflowY = el.scrollHeight > 120 ? "auto" : "hidden";
}

function resetTextarea() {
  if (textareaRef.value) {
    textareaRef.value.style.height = "auto";
    textareaRef.value.style.overflowY = "hidden";
  }
}
</script>

<style scoped>
.chat-window {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  background: var(--color-bg);
}

/* ── Header ── */
.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 24px;
  background: var(--color-surface);
  border-bottom: 1px solid var(--color-border);
  gap: 12px;
  flex-wrap: wrap;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.kb-icon {
  font-size: 22px;
}

.kb-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--color-text);
}

.kb-subtitle {
  font-size: 12px;
  color: var(--color-text-muted);
  margin-top: 1px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.btn-clear {
  padding: 6px 14px;
  background: transparent;
  color: #ef4444;
  border: 1.5px solid #fca5a5;
  border-radius: 7px;
  font-size: 13px;
  transition: background 0.15s;
}

.btn-clear:hover {
  background: #fee2e2;
}

/* ── Messages ── */
.message-area {
  flex: 1;
  min-height: 0;      /* required: lets flex child shrink and become scrollable */
  overflow-y: auto;
  padding: 24px 32px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.empty-state {
  margin: auto;
  text-align: center;
  color: var(--color-text-muted);
  padding: 40px 20px;
}

.empty-icon {
  font-size: 48px;
  margin-bottom: 16px;
}

.empty-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--color-text);
  margin-bottom: 8px;
}

.empty-desc {
  font-size: 14px;
  line-height: 1.7;
}

.message {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  max-width: 780px;
}

.message.user {
  flex-direction: row-reverse;
  align-self: flex-end;
}

.message.ai {
  align-self: flex-start;
  align-items: flex-start;
}

.ai-content {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: 680px;
}

.avatar {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  flex-shrink: 0;
}

.user-avatar {
  background: var(--color-primary);
  color: #fff;
  font-size: 12px;
  font-weight: 700;
}

.ai-avatar {
  background: #e2e8f0;
  font-size: 18px;
}

.bubble {
  padding: 12px 16px;
  border-radius: 14px;
  font-size: 14px;
  line-height: 1.7;
  word-break: break-word;
}

.user-bubble {
  background: var(--color-user-bubble);
  color: #fff;
  border-bottom-right-radius: 4px;
}

.ai-bubble {
  background: var(--color-surface);
  color: var(--color-text);
  border: 1px solid var(--color-border);
  border-bottom-left-radius: 4px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.05);
}

/* AI bubble markdown styles */
.ai-bubble :deep(p) { margin: 0 0 8px; }
.ai-bubble :deep(p:last-child) { margin-bottom: 0; }
.ai-bubble :deep(ul), .ai-bubble :deep(ol) { padding-left: 20px; margin: 6px 0; }
.ai-bubble :deep(li) { margin-bottom: 4px; }
.ai-bubble :deep(code) {
  background: #f1f5f9;
  padding: 1px 5px;
  border-radius: 4px;
  font-family: "Consolas", monospace;
  font-size: 13px;
}
.ai-bubble :deep(pre) {
  background: #1e293b;
  color: #e2e8f0;
  padding: 12px;
  border-radius: 8px;
  overflow-x: auto;
  margin: 8px 0;
}
.ai-bubble :deep(pre code) {
  background: none;
  color: inherit;
  padding: 0;
}
.ai-bubble :deep(blockquote) {
  border-left: 3px solid var(--color-primary);
  padding-left: 12px;
  color: var(--color-text-muted);
  margin: 6px 0;
}

/* Streaming cursor */
.ai-bubble :deep(.streaming-cursor) {
  display: inline-block;
  color: var(--color-primary);
  animation: blink 1s step-end infinite;
  font-weight: 400;
  line-height: 1;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

/* ── Input ── */
.input-area {
  padding: 16px 24px;
  background: var(--color-surface);
  border-top: 1px solid var(--color-border);
}

.input-box {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  background: #f8fafc;
  border: 1.5px solid var(--color-border);
  border-radius: 12px;
  padding: 10px 12px;
  transition: border-color 0.15s;
}

.input-box:focus-within {
  border-color: var(--color-primary);
}

.input-box textarea {
  flex: 1;
  border: none;
  background: transparent;
  font-size: 14px;
  color: var(--color-text);
  resize: none;
  line-height: 1.6;
  max-height: 120px;
  overflow-y: hidden;
  padding-top: 2px;
  vertical-align: top;
}

.input-box textarea::placeholder {
  color: #94a3b8;
}

.btn-send {
  padding: 7px 20px;
  background: var(--color-primary);
  color: #fff;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 600;
  flex-shrink: 0;
  transition: background 0.15s;
  align-self: flex-end;
}

.btn-send:hover:not(:disabled) {
  background: var(--color-primary-hover);
}

.btn-send:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ── Files bar ── */
.files-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 24px;
  background: #f8fafc;
  border-bottom: 1px solid var(--color-border);
  overflow-x: auto;
  flex-shrink: 0;
}

.files-bar-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--color-text-muted);
  white-space: nowrap;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.files-chips {
  display: flex;
  gap: 6px;
  flex-wrap: nowrap;
}

.file-chip {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 6px 3px 8px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 20px;
  font-size: 12px;
  white-space: nowrap;
}

.file-chip-icon {
  font-size: 11px;
}

.file-chip-name {
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--color-text);
  font-weight: 500;
}

.file-chip-name.has-summary {
  cursor: pointer;
  color: var(--color-primary);
  text-decoration: underline;
  text-decoration-style: dotted;
  text-underline-offset: 2px;
}

.file-chip-name.has-summary:hover {
  opacity: 0.8;
}

.file-chip-count {
  color: var(--color-text-muted);
  font-size: 11px;
}

.file-chip-delete {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: transparent;
  color: #94a3b8;
  font-size: 14px;
  line-height: 1;
  padding: 0;
  margin-left: 2px;
  transition: background 0.1s, color 0.1s;
}

.file-chip-delete:hover {
  background: #fee2e2;
  color: #ef4444;
}

/* ── Summary panel ── */
.summary-panel {
  padding: 10px 24px;
  background: #fffbeb;
  border-bottom: 1px solid #fde68a;
  flex-shrink: 0;
}

.summary-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.summary-panel-title {
  font-size: 12px;
  font-weight: 600;
  color: #92400e;
}

.summary-panel-close {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: transparent;
  color: #92400e;
  font-size: 15px;
  line-height: 1;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}

.summary-panel-close:hover {
  background: #fde68a;
}

.summary-panel-body {
  font-size: 13px;
  color: #78350f;
  line-height: 1.65;
}

.summary-pending {
  font-style: italic;
  color: #a16207;
}

/* ── Sources ── */
.sources {
  padding: 0 2px;
}

.sources-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 6px;
}

.sources-list {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.source-item {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  padding: 7px 10px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  transition: background 0.1s;
}

.source-doc {
  background: #f8fafc;
}

.source-doc:hover {
  background: #f1f5f9;
}

.source-web {
  background: #f0fdf4;
  border-color: #bbf7d0;
}

.source-web:hover {
  background: #dcfce7;
}

.source-icon {
  font-size: 13px;
  flex-shrink: 0;
  margin-top: 1px;
}

.source-body {
  min-width: 0;
}

.source-filename {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.source-title {
  font-size: 12px;
  font-weight: 600;
  color: #15803d;
  text-decoration: none;
  display: block;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.source-title:hover {
  text-decoration: underline;
}

.source-url {
  font-size: 10px;
  color: #86efac;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-top: 1px;
  color: #4ade80;
}

.source-preview {
  font-size: 11px;
  color: var(--color-text-muted);
  line-height: 1.5;
  margin-top: 2px;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
</style>
