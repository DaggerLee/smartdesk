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
          <div class="bubble ai-bubble" v-html="renderMessageContent(msg)"></div>
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
import { nextTick, onMounted, ref, watch } from "vue";
import { clearChatHistory, getChatHistory, sendMessageStream } from "../api/index.js";
import FileUpload from "./FileUpload.vue";

const props = defineProps({
  kb: { type: Object, required: true },
});

const messages = ref([]);
const inputText = ref("");
const sending = ref(false);
const loading = ref(false);
const messageArea = ref(null);
const textareaRef = ref(null);

// Reload history whenever the active knowledge base changes
watch(
  () => props.kb.id,
  () => loadHistory(),
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
  messages.value.push({ id: tempId, question: text, answer: "", streaming: true });
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
      () => {
        const idx = messages.value.findIndex((m) => m.id === tempId);
        if (idx !== -1) messages.value[idx].streaming = false;
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

function onFileUploaded() {
  // File upload feedback is handled inside the FileUpload component
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
  el.style.height = Math.min(el.scrollHeight, 140) + "px";
}

function resetTextarea() {
  if (textareaRef.value) {
    textareaRef.value.style.height = "auto";
  }
}
</script>

<style scoped>
.chat-window {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
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
  max-width: 680px;
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
  max-height: 140px;
  overflow-y: auto;
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
</style>
