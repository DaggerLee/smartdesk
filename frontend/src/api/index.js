import axios from "axios";

// ── Token helpers ─────────────────────────────────────────────────────────────

export function getToken() {
  return localStorage.getItem("smartdesk_token") || "";
}

export function getStoredUsername() {
  return localStorage.getItem("smartdesk_username") || "";
}

export function saveAuth(token, username) {
  localStorage.setItem("smartdesk_token", token);
  localStorage.setItem("smartdesk_username", username);
}

export function clearAuth() {
  localStorage.removeItem("smartdesk_token");
  localStorage.removeItem("smartdesk_username");
}

// ── Axios instance ────────────────────────────────────────────────────────────

const api = axios.create({ baseURL: "/api" });

// Attach the Bearer token to every request
api.interceptors.request.use((config) => {
  const token = getToken();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// On 401, clear stored credentials and reload so the app shows the login page
api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401) {
      clearAuth();
      window.location.reload();
    }
    return Promise.reject(err);
  }
);

// ── Auth ──────────────────────────────────────────────────────────────────────

export async function login(username, password) {
  const { data } = await api.post("/auth/login", { username, password });
  saveAuth(data.access_token, data.username);
  return data;
}

export async function register(username, password) {
  const { data } = await api.post("/auth/register", { username, password });
  saveAuth(data.access_token, data.username);
  return data;
}

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

export function listFiles(kbId) {
  return api.get(`/knowledge-base/${kbId}/files`).then((r) => r.data);
}

export function deleteFile(kbId, filename) {
  return api.delete(`/knowledge-base/${kbId}/files/${encodeURIComponent(filename)}`).then((r) => r.data);
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
async function ensureStreamResponse(response) {
  if (response.ok) return true;
  if (response.status === 401) {
    clearAuth();
    window.location.reload();
    return false;
  }
  const err = await response.json().catch(() => ({ detail: "Unknown error" }));
  throw new Error(err.detail || "Request failed");
}


async function consumeEventStream(
  response,
  callbacks,
) {
  const {
    onChunk,
    onSources,
    onDone,
    onStatus,
    onPaused,
    onFailed,
    onConfirmation,
    onActionResult,
    doneOnEof = true,
  } = callbacks;
  if (!(await ensureStreamResponse(response))) return;

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let bufferedChunks = [];


  while (true) {
    let readResult;
    try {
      readResult = await reader.read();
    } catch {
      if (doneOnEof) throw new Error("Stream interrupted");
      onFailed?.();
      return;
    }
    const { done, value } = readResult;
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop();

    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      const data = line.slice(6).trim();
      if (data === "[DONE]") {
        for (const chunk of bufferedChunks) onChunk?.(chunk);
        bufferedChunks = [];
        onDone?.();
        return;
      }
      if (data === "[PAUSED]") {
        onPaused?.();
        return;
      }
      if (data === "[FAILED]") {
        bufferedChunks = [];
        onFailed?.();
        return;
      }
      try {
        const parsed = JSON.parse(data);
        if (typeof parsed === "string") {
          if (doneOnEof) onChunk?.(parsed);
          else bufferedChunks.push(parsed);
        } else if (parsed.sources) {
          onSources?.(parsed.sources);
        } else if (parsed.status) {
          onStatus?.(parsed.status);
        } else if (parsed.confirmation_required) {
          onConfirmation?.(parsed.confirmation_required);
        } else if (parsed.action_result) {
          onActionResult?.(parsed.action_result);
        }
      } catch {
        // skip malformed lines
      }
    }
  }
  if (doneOnEof) onDone?.();
  else onFailed?.();
}


export async function sendMessageStream(
  kbId,
  message,
  onChunk,
  onSources,
  onDone,
  onStatus,
  onPaused,
  onFailed,
  onConfirmation,
) {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${getToken()}`,
    },
    body: JSON.stringify({ kb_id: kbId, message }),
  });

  await consumeEventStream(response, {
    onChunk,
    onSources,
    onDone,
    onStatus,
    onPaused,
    onFailed,
    onConfirmation,
  });
}


export async function resolveActionStream(threadId, resolution, callbacks = {}) {
  const response = await fetch(
    `/api/chat/actions/${encodeURIComponent(threadId)}/resolve`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${getToken()}`,
      },
      body: JSON.stringify(resolution),
    },
  );

  await consumeEventStream(response, { ...callbacks, doneOnEof: false });
}

export function getChatHistory(kbId) {
  return api.get(`/chat/history/${kbId}`).then((r) => r.data);
}

export function clearChatHistory(kbId) {
  return api.delete(`/chat/history/${kbId}`).then((r) => r.data);
}
