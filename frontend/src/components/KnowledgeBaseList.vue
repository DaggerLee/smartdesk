<template>
  <aside class="sidebar">
    <!-- Logo -->
    <div class="sidebar-header">
      <span class="logo-icon">🤖</span>
      <span class="logo-text">SmartDesk</span>
    </div>

    <div class="sidebar-actions">
      <button class="btn-new" @click="showModal = true">
        <span>＋</span> New Knowledge Base
      </button>
    </div>

    <div class="kb-list">
      <div v-if="loading" class="empty-tip">Loading…</div>
      <div v-else-if="knowledgeBases.length === 0" class="empty-tip">
        No knowledge bases yet. Click the button above to create one.
      </div>
      <div
        v-for="kb in knowledgeBases"
        :key="kb.id"
        class="kb-item"
        :class="{ active: selectedId === kb.id }"
        @click="$emit('select', kb)"
      >
        <div class="kb-item-body">
          <div class="kb-name">{{ kb.name }}</div>
          <div class="kb-desc" v-if="kb.description">{{ kb.description }}</div>
        </div>
        <button
          class="btn-delete"
          title="Delete knowledge base"
          @click.stop="handleDelete(kb)"
        >
          ✕
        </button>
      </div>
    </div>

    <div v-if="showModal" class="modal-mask" @click.self="showModal = false">
      <div class="modal">
        <h3>New Knowledge Base</h3>
        <label>Name <span class="required">*</span></label>
        <input
          v-model="form.name"
          placeholder="e.g. Product Manual, FAQ"
          @keydown.enter="handleCreate"
        />
        <label>Description (optional)</label>
        <input v-model="form.description" placeholder="Briefly describe the purpose of this knowledge base" />
        <div class="modal-actions">
          <button class="btn-cancel" @click="showModal = false">Cancel</button>
          <button class="btn-confirm" :disabled="creating" @click="handleCreate">
            {{ creating ? "Creating…" : "Create" }}
          </button>
        </div>
      </div>
    </div>
  </aside>
</template>

<script setup>
import { ref } from "vue";
import { createKnowledgeBase, deleteKnowledgeBase } from "../api/index.js";

const props = defineProps({
  knowledgeBases: { type: Array, default: () => [] },
  selectedId: { type: Number, default: null },
  loading: { type: Boolean, default: false },
});

const emit = defineEmits(["select", "refresh"]);

const showModal = ref(false);
const creating = ref(false);
const form = ref({ name: "", description: "" });

async function handleCreate() {
  if (!form.value.name.trim()) return;
  creating.value = true;
  try {
    const newKb = await createKnowledgeBase(form.value.name.trim(), form.value.description.trim());
    form.value = { name: "", description: "" };
    showModal.value = false;
    emit("refresh");
    emit("select", newKb);
  } finally {
    creating.value = false;
  }
}

async function handleDelete(kb) {
  if (!confirm(`Delete knowledge base "${kb.name}"? This action cannot be undone.`)) return;
  await deleteKnowledgeBase(kb.id);
  emit("refresh");
}
</script>

<style scoped>
.sidebar {
  width: var(--sidebar-width);
  min-width: var(--sidebar-width);
  background: var(--color-sidebar-bg);
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
}

.sidebar-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 20px 18px;
  border-bottom: 1px solid #2d3f55;
}

.logo-icon {
  font-size: 22px;
}

.logo-text {
  font-size: 18px;
  font-weight: 700;
  color: #f1f5f9;
  letter-spacing: 0.5px;
}

.sidebar-actions {
  padding: 14px 12px 10px;
}

.btn-new {
  width: 100%;
  padding: 9px 14px;
  background: var(--color-primary);
  color: #fff;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  transition: background 0.15s;
}

.btn-new:hover {
  background: var(--color-primary-hover);
}

.kb-list {
  flex: 1;
  overflow-y: auto;
  padding: 6px 8px;
}

.empty-tip {
  color: #64748b;
  font-size: 13px;
  text-align: center;
  margin-top: 24px;
  line-height: 1.6;
  padding: 0 12px;
}

.kb-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 10px;
  border-radius: 8px;
  cursor: pointer;
  color: var(--color-sidebar-text);
  transition: background 0.12s;
  margin-bottom: 2px;
}

.kb-item:hover {
  background: var(--color-sidebar-hover);
}

.kb-item.active {
  background: #1e40af;
  color: #fff;
}

.kb-item-body {
  flex: 1;
  min-width: 0;
}

.kb-name {
  font-size: 14px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.kb-desc {
  font-size: 11px;
  color: #94a3b8;
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.kb-item.active .kb-desc {
  color: #bfdbfe;
}

.btn-delete {
  background: transparent;
  color: #64748b;
  font-size: 11px;
  padding: 2px 5px;
  border-radius: 4px;
  flex-shrink: 0;
  opacity: 0;
  transition: opacity 0.12s, color 0.12s;
}

.kb-item:hover .btn-delete {
  opacity: 1;
}

.btn-delete:hover {
  color: #f87171;
}

/* Modal */
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 999;
}

.modal {
  background: #fff;
  border-radius: 12px;
  padding: 28px 28px 24px;
  width: 380px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.25);
}

.modal h3 {
  font-size: 17px;
  font-weight: 700;
  color: var(--color-text);
  margin-bottom: 4px;
}

.modal label {
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text-muted);
}

.required {
  color: #ef4444;
}

.modal input {
  border: 1.5px solid var(--color-border);
  border-radius: 7px;
  padding: 9px 12px;
  font-size: 14px;
  width: 100%;
  transition: border-color 0.15s;
}

.modal input:focus {
  border-color: var(--color-primary);
}

.modal-actions {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
  margin-top: 6px;
}

.btn-cancel {
  padding: 8px 18px;
  border-radius: 7px;
  background: var(--color-bg);
  color: var(--color-text-muted);
  font-size: 14px;
}

.btn-cancel:hover {
  background: var(--color-border);
}

.btn-confirm {
  padding: 8px 22px;
  border-radius: 7px;
  background: var(--color-primary);
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  transition: background 0.15s;
}

.btn-confirm:hover:not(:disabled) {
  background: var(--color-primary-hover);
}

.btn-confirm:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
