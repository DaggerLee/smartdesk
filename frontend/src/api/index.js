import axios from "axios";

const api = axios.create({ baseURL: "/api" });

// ── Knowledge Base ────────────────────────────────────────────────────────────

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

// ── Chat ──────────────────────────────────────────────────────────────────────

export async function sendMessageStream(kbId, message, onChunk, onSources, onDone) {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kb_id: kbId, message }),
  });

  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: "Unknown error" }));
    throw new Error(err.detail || "Request failed");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop(); // keep any incomplete trailing line

    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      const data = line.slice(6).trim();
      if (data === "[DONE]") {
        onDone?.();
        return;
      }
      try {
        const parsed = JSON.parse(data);
        if (typeof parsed === "string") {
          onChunk?.(parsed);
        } else if (parsed.sources) {
          onSources?.(parsed.sources);
        }
      } catch {
        // skip malformed lines
      }
    }
  }
  onDone?.();
}

export function getChatHistory(kbId) {
  return api.get(`/chat/history/${kbId}`).then((r) => r.data);
}

export function clearChatHistory(kbId) {
  return api.delete(`/chat/history/${kbId}`).then((r) => r.data);
}
