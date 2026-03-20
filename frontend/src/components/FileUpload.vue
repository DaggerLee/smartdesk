<template>
  <div class="upload-area">
    <input
      ref="inputRef"
      type="file"
      accept=".pdf,.txt"
      class="hidden-input"
      @change="handleChange"
    />

    <button class="btn-upload" :disabled="uploading" @click="inputRef.click()">
      <span class="icon">📎</span>
      {{ uploading ? `Uploading ${progress}%` : "Upload File" }}
    </button>

    <!-- 上传进度条 -->
    <div v-if="uploading" class="progress-bar">
      <div class="progress-fill" :style="{ width: progress + '%' }"></div>
    </div>

    <!-- 结果提示 -->
    <transition name="fade">
      <div v-if="resultMsg" class="result-msg" :class="resultType">
        {{ resultMsg }}
      </div>
    </transition>
  </div>
</template>

<script setup>
import { ref } from "vue";
import { uploadFile } from "../api/index.js";

const props = defineProps({
  kbId: { type: Number, required: true },
});

const emit = defineEmits(["uploaded"]);

const inputRef = ref(null);
const uploading = ref(false);
const progress = ref(0);
const resultMsg = ref("");
const resultType = ref("success");

async function handleChange(e) {
  const file = e.target.files?.[0];
  if (!file) return;

  uploading.value = true;
  progress.value = 0;
  resultMsg.value = "";

  try {
    const res = await uploadFile(props.kbId, file, (p) => {
      progress.value = p;
    });
    resultType.value = "success";
    resultMsg.value = `✓ Uploaded: ${res.filename} (${res.chunks} chunks)`;
    emit("uploaded");
  } catch (err) {
    resultType.value = "error";
    resultMsg.value = `✗ Upload failed: ${err.response?.data?.detail || err.message}`;
  } finally {
    uploading.value = false;
    progress.value = 0;
    // 重置 input，允许重复上传同名文件
    if (inputRef.value) inputRef.value.value = "";
    // 3 秒后清除提示
    setTimeout(() => (resultMsg.value = ""), 4000);
  }
}
</script>

<style scoped>
.upload-area {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.hidden-input {
  display: none;
}

.btn-upload {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  background: #f1f5f9;
  color: var(--color-text);
  border: 1.5px solid var(--color-border);
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  transition: background 0.15s, border-color 0.15s;
}

.btn-upload:hover:not(:disabled) {
  background: #e2e8f0;
  border-color: #94a3b8;
}

.btn-upload:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.icon {
  font-size: 15px;
}

.progress-bar {
  width: 100px;
  height: 4px;
  background: #e2e8f0;
  border-radius: 2px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: var(--color-primary);
  border-radius: 2px;
  transition: width 0.2s;
}

.result-msg {
  font-size: 12px;
  padding: 4px 10px;
  border-radius: 6px;
}

.result-msg.success {
  background: #dcfce7;
  color: #166534;
}

.result-msg.error {
  background: #fee2e2;
  color: #991b1b;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
