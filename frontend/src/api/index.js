import axios from "axios";

const api = axios.create({ baseURL: "/api" });

// ── 知识库 ────────────────────────────────────────────────────────────────────

export function listKnowledgeBases() {
  return api.get("/knowledge-base").then((r) => r.data);
}

export function createKnowledgeBase(name, description = "") {
  return api.post("/knowledge-base", { name, description }).then((r) => r.data);
}

export function deleteKnowledgeBase(id) {
  return api.delete(`/knowledge-base/${id}`).then((r) => r.data);
}

export function uploadFile(kbId, file, onProgress) {
  const form = new FormData();
  form.append("file", file);
  return api
    .post(`/knowledge-base/${kbId}/upload`, form, {
      headers: { "Content-Type": "multipart/form-data" },
      onUploadProgress: (e) => {
        if (onProgress && e.total) onProgress(Math.round((e.loaded * 100) / e.total));
      },
    })
    .then((r) => r.data);
}

// ── 对话 ──────────────────────────────────────────────────────────────────────

export function sendMessage(kbId, message) {
  return api.post("/chat", { kb_id: kbId, message }).then((r) => r.data);
}

export function getChatHistory(kbId) {
  return api.get(`/chat/history/${kbId}`).then((r) => r.data);
}

export function clearChatHistory(kbId) {
  return api.delete(`/chat/history/${kbId}`).then((r) => r.data);
}
