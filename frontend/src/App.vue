<template>
  <div class="layout">
    <!-- 左侧：知识库列表 -->
    <KnowledgeBaseList
      :knowledge-bases="knowledgeBases"
      :selected-id="selectedKB?.id ?? null"
      :loading="loadingKBs"
      @select="selectKB"
      @refresh="loadKnowledgeBases"
    />

    <!-- 右侧：对话窗口 -->
    <main class="main">
      <ChatWindow v-if="selectedKB" :kb="selectedKB" />

      <!-- 未选择知识库时的欢迎页 -->
      <div v-else class="welcome">
        <div class="welcome-card">
          <div class="welcome-icon">🤖</div>
          <h1>SmartDesk</h1>
          <p>
            Select or create a knowledge base on the left,<br />
            then upload PDF / TXT documents to start chatting with AI.
          </p>
          <div class="feature-list">
            <div class="feature">📂 Knowledge Base</div>
            <div class="feature">🔍 Semantic Search (RAG)</div>
            <div class="feature">💬 Chat History</div>
          </div>
        </div>
      </div>
    </main>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { listKnowledgeBases } from "./api/index.js";
import ChatWindow from "./components/ChatWindow.vue";
import KnowledgeBaseList from "./components/KnowledgeBaseList.vue";

const knowledgeBases = ref([]);
const selectedKB = ref(null);
const loadingKBs = ref(false);

onMounted(() => loadKnowledgeBases());

async function loadKnowledgeBases() {
  loadingKBs.value = true;
  try {
    knowledgeBases.value = await listKnowledgeBases();
    // 若之前选择的知识库仍存在，保持选中
    if (selectedKB.value) {
      const found = knowledgeBases.value.find((kb) => kb.id === selectedKB.value.id);
      selectedKB.value = found ?? null;
    }
  } catch (e) {
    console.error("加载知识库失败", e);
  } finally {
    loadingKBs.value = false;
  }
}

function selectKB(kb) {
  selectedKB.value = kb;
}
</script>

<style scoped>
.layout {
  display: flex;
  width: 100%;
  height: 100vh;
  overflow: hidden;
}

.main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.welcome {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-bg);
}

.welcome-card {
  text-align: center;
  padding: 48px 56px;
  background: var(--color-surface);
  border-radius: 20px;
  border: 1px solid var(--color-border);
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.06);
  max-width: 480px;
}

.welcome-icon {
  font-size: 56px;
  margin-bottom: 20px;
}

.welcome-card h1 {
  font-size: 24px;
  font-weight: 700;
  color: var(--color-text);
  margin-bottom: 12px;
}

.welcome-card p {
  color: var(--color-text-muted);
  font-size: 15px;
  line-height: 1.8;
  margin-bottom: 28px;
}

.feature-list {
  display: flex;
  gap: 10px;
  justify-content: center;
  flex-wrap: wrap;
}

.feature {
  padding: 7px 16px;
  background: #eff6ff;
  color: var(--color-primary);
  border-radius: 20px;
  font-size: 13px;
  font-weight: 500;
}
</style>
