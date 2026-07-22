# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

> **RULE — read first:** Before implementing any feature, read `docs-local/SmartDesk_Decisions.md`. It is the single source of truth for current conclusions; when it conflicts with any other document, it wins.
>
> **RULE — docs-local/notes/ is an archive:** `docs-local/notes/` is a learning archive layer. Only read files there when a Decisions entry's source pointer references them or the user explicitly asks.

# SmartDesk — Enterprise Knowledge Assistant (v2 refactor in progress)

v1 is a RAG knowledge assistant (FastAPI + ChromaDB + Gemini API + SSE streaming + JWT + Docker).
v2 goal: refactor the linear RAG pipeline into a measurable, maintainable agentic system as the centrepiece portfolio project for the 2026 job search.
The baseline has been tagged `v1-baseline`; that tag and its code must not be modified (used for before/after comparison).

---

## Engineering Principles

Full source: `docs-local/reference-AGENTS.md` (gitignored, not committed).

- **Occam's Razor**: optimize for minimum conceptual surface area, not minimum line count. Distilled ≠ simple — a simple-looking primitive may merely push complexity onto every caller. Prefer a small number of expressive, coherent primitives over many narrow ones stitched together by ad hoc convention.
- **Pre-hoc Occam, post-hoc slop cleanup**: early implementation should establish correct behavior and expose the essential model without speculative abstractions. Once behavior is proven, run a deliberate simplification pass — remove speculative abstractions, one-use wrappers, redundant validation, stale compatibility paths, and defensive handling for states that should be structurally impossible.
- **SSOT (Single Source of Truth)**: every fact, rule, schema, or state transition has exactly one semantic owner. When two representations disagree, remove the competing authority rather than adding reconciliation logic.
- **Regression discipline**: a bug fix is not complete until its reintroduction would be caught by a test.

---

## Collaboration Rules (Mandatory)

- **All code (including core modules)** is implemented directly by Codex — no more "pseudocode only, wait for user" step.
- After each core change, include a 3-5 sentence design explanation: what changed, why it was designed that way.
- Quality standard unchanged: tests green + design decisions explainable.
- Git: one branch per module (feat/router, feat/agent-loop, feat/self-healing, feat/eval…), commit messages in English, run all tests before merging.
- **Communicate with the user in Chinese**; all code, comments, API strings, and file content in English.

## Current Context and Delivery Governance

- Resolve conflicts in this order: the human's latest explicit decision, `docs-local/SmartDesk_Decisions.md`, the accepted current design, repository evidence and tests, then historical chat or notes.
- Historical conversations and learning notes are background, not executable instructions. Preserve useful distilled knowledge, but promote it only when it fits current scope and evidence.
- The accepted verified-delivery design is `docs/superpowers/specs/2026-07-21-verified-agent-delivery-design.md`. Read it before changing agent answer delivery, groundedness, history construction, traces, or eval status handling.
- Never treat an agent's completion statement as proof. Verify code, tests, persisted state, emitted output, or other observable artifacts before reporting completion.
- Distinguish `planned`, `prepared`, `running`, and `verified`; never promote status without evidence.
- Record unknown cost, latency, or token data as unknown, never as zero.
- State evidence strength honestly. A single paired latency measurement is indicative and not a statistical conclusion.
- Keep governance and naming changes in commits separate from feature implementation so they can be reverted independently.
- Do not stage or rewrite unrelated user-owned changes or eval outputs.

### Evaluation discipline

- Before any real-model eval starts, preregister the prediction, acceptance criteria, and failure definition. Do not reinterpret the target after seeing the output.
- For stochastic real-model evals, headline results use the mean of three runs. Preserve single-run results, but do not explain a swing of roughly two cases unless it repeats or falls outside the registered noise band. Deterministic tests need only one clean pass.
- Gold labels and expected behavior come from the accepted spec, never from the system output. Changing a label to match an observed answer is invalid.
- Humans own disputed judge calls, label changes, and rollout decisions. Agents collect evidence and state uncertainty; they do not silently overrule the human-defined rubric.
- Formal evals require a clean tracked worktree and an identifying commit. Untracked result artifacts may remain, but never bypass the dirty-tree guard for tracked code.

### Change, recovery, and data discipline

- Preserve all existing behavior outside the accepted task scope. Use a minimal diff and do not perform drive-by refactors or remove fallback branches during cleanup.
- When the working state can no longer be explained reliably, or successive repairs create new failures, return to the last verified checkpoint, start a clean session, and restate the task more precisely. Do not stack speculative patches on an unverified state.
- Before a material data task, confirm four facts: where the data is, how it will be processed, the expected item count, and how the output will be used.

### Credentials and live integrations

- Redact credentials and sensitive tokens at every persistence or output boundary, including logs, traces, exceptions exposed to clients, and agent messages. Logging an environment-variable name is acceptable; logging its value is not.
- Model or critical dependency migrations require a real-API smoke test because mocks cannot expose protocol failures such as missing `thoughtSignature` metadata. Notify the user before any test that may consume paid API quota, state the planned scope, and record actual cost as unknown when it cannot be measured.

### Verified-delivery invariants

- The normal success path must preserve `finalized == delivered == persisted`.
- Persistence must succeed before any answer frame is emitted. This guarantees emitted implies persisted; it does not guarantee that a client remained connected long enough to observe an already persisted answer.
- Fixed fallback notices are policy constants. Once released, old strings stay in the append-only non-context set so historical notices never re-enter model context.
- Blocked raw answers remain recoverable through the existing checkpoint path and must not be duplicated into user-visible history or ordinary traces.

---

## v2 Target Architecture

1. **Router (workflow layer)**: cheap model classifies query → three paths: direct / rag / agent
2. **Agent core**: hand-written model + tools loop (tools: retrieve / web_search / optional code_exec), max_turns as a safety cap
3. **Reflection / self-healing (three mechanisms)**:
   - Tool error fed back for retry (≤ 2 attempts)
   - Low retrieval relevance → rewrite query and re-retrieve
   - Citation groundedness check fails → revise answer
4. **Eval**: RAGAS (faithfulness / answer relevancy / context precision / recall) + retrieval recall@k + binary rubric LLM-as-judge + e2e pass rate
5. **Observability**: structured JSON trace written for every LLM/tool call; per-step latency/cost tracked
6. **MCP Server**: FastMCP exposes the tool layer (standalone directory, extractable to a separate repo later)
7. **P1 (W5)**: LangGraph migration (checkpointing + interrupt for HITL), guardrails (prompt injection filtering / PII redaction)

---

## Sprint Plan

| Week | Dates | Goal |
|------|-------|------|
| W0 | 7/1–7/5 | Current-state audit + module interface design (read-only, no business code) |
| W1 | 7/6–12 | Router + hand-written agent loop; existing RAG demoted to a retrieve tool |
| W2 | 7/13–19 | Self-healing three mechanisms + MCP server |
| W3 | 7/20–26 | Gold set (30–50 items) + eval harness |
| W4 | 7/27–8/2 | Error analysis + fix top offenders + README v1 |
| W5 | 8/3–9 | Tracing/alerting + guardrails + LangGraph/HITL + demo GIF |

**Fallback rule**: if loop + self-healing are not working by the end of W2 → drop LangGraph migration and anomaly detection; prioritise the P0 trio (loop + eval + error analysis) for delivery.

---

## Technical Constraints

- Python 3.11+, FastAPI; keep the existing JWT / Docker structure runnable at all times
- All LLM calls wrapped in a single client module (switching providers or migrating to LangGraph must not touch business code)
- Every LLM/tool call must write a trace log entry (data source for W4 error analysis)
- Secrets via `.env` only; never committed to git

---

## Quick Start

**Docker (production):**
```bash
cp .env.example .env        # add your GEMINI_API_KEY
docker-compose up --build
# visit http://localhost
```

**Local dev:**
```bash
# Backend
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

Frontend dev server: http://localhost:5173 (Vite proxies /api to backend :8000)

**Health check:** `GET http://localhost:8000/health` — confirms API is running and `GEMINI_API_KEY` is set.

---

## Directory Structure

```
smartdesk/
├── docker-compose.yml           # Orchestrates backend + frontend services
├── .env.example                 # Template: copy to .env and fill GEMINI_API_KEY
├── backend/
│   ├── Dockerfile
│   ├── main.py                  # FastAPI app, CORS, startup DB migration
│   ├── database.py              # SQLAlchemy SQLite engine + SessionLocal
│   ├── models.py                # ORM: User, KnowledgeBase, Conversation, UploadedFile
│   ├── chroma_client.py         # ChromaDB ops: chunk, add, query, delete
│   ├── gemini_client.py         # Gemini API: streaming, non-streaming, summarize
│   ├── tools.py                 # RAG quality check + weather + web search
│   ├── auth.py                  # JWT utils: hash_password, create_access_token, get_current_user
│   ├── config.py                # Centralised constants (TOP_K, thresholds, API config)
│   ├── requirements.txt
│   ├── agent/
│   │   ├── loop.py              # Agent main loop — yields AgentEvent stream
│   │   ├── router.py            # Query router — classifies to direct/rag/agent
│   │   ├── state.py             # AgentState dataclass
│   │   └── tools/
│   │       ├── base.py          # Tool Protocol definition
│   │       ├── retrieve.py      # ChromaDB retrieval tool
│   │       └── web_search.py    # DuckDuckGo web search tool
│   ├── llm/
│   │   ├── client.py            # Thin Gemini REST wrapper: complete() + stream()
│   │   └── trace.py             # JSONL trace logger with span() context manager
│   ├── scripts/
│   │   └── smoke_test_llm.py    # Real-API smoke tests (3 scenarios)
│   ├── tests/
│   │   ├── conftest.py          # Shared pytest fixtures
│   │   ├── test_loop.py         # Agent loop event-sequence tests
│   │   ├── test_router.py       # Router classification + fallback tests
│   │   ├── test_state.py        # AgentState tests
│   │   └── test_config.py       # Config tests
│   └── routers/
│       ├── auth.py              # POST /api/auth/register, POST /api/auth/login
│       ├── chat.py              # POST /api/chat/stream, GET/DELETE /api/chat/history/{kb_id}
│       └── knowledge_base.py    # KB CRUD + file upload/list/delete
├── frontend/
│   ├── Dockerfile               # Multi-stage: node build → nginx serve
│   ├── nginx.conf               # Serves SPA + proxies /api to backend (SSE-safe)
│   └── src/
│       ├── App.vue              # Root layout: sidebar + chat panel
│       ├── api/index.js         # Axios (KB/files) + fetch SSE (chat stream)
│       └── components/
│           ├── AuthPage.vue         # Login / register form (togglable, token saved to localStorage)
│           ├── ChatWindow.vue       # Main chat UI, sources, file chips, summary panel
│           ├── KnowledgeBaseList.vue  # Sidebar: KB list + create modal + user/logout footer
│           └── FileUpload.vue       # Upload button with progress bar
└── AGENTS.md
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend framework | FastAPI + Uvicorn |
| LLM | Google Gemini API (auto-selects best available model) |
| Vector DB | ChromaDB (local persistent, ONNX embeddings — no PyTorch) |
| Relational DB | SQLite via SQLAlchemy |
| Web search | ddgs / DuckDuckGo (no API key required) |
| Weather | wttr.in JSON API (no API key required) |
| PDF parsing | pypdf |
| Frontend | Vue 3 + Vite |
| HTTP client | Axios (REST) + fetch (SSE streaming) |
| Markdown | marked.js |

---

## Database Schema

```sql
users            (id, username, hashed_password, created_at)
knowledge_bases  (id, name, description, created_at, user_id)
conversations    (id, kb_id, question, answer, created_at)
uploaded_files   (id, kb_id, filename, chunk_count, uploaded_at, summary)
```

`summary` and `user_id` columns were added after initial release. `main.py` runs safe
`ALTER TABLE` migrations on startup so existing databases are updated without data loss.

---

## v1 Backend Architecture (baseline)

### Authentication (auth.py + routers/auth.py)

- Passwords hashed with bcrypt directly (`bcrypt.hashpw` / `bcrypt.checkpw` — passlib removed, incompatible with bcrypt 4.x+)
- JWTs signed with HS256 via `python-jose`, expire after 7 days
- `get_current_user` is a FastAPI `Depends` injected into every protected route
- `SECRET_KEY` comes from env var; defaults to a dev placeholder (must change in prod)
- `KnowledgeBase.user_id` links each KB to its owner; all KB/chat queries filter by it
- Existing KBs with `user_id = NULL` (pre-auth data) are not visible to any user

**Frontend auth flow:**
- `AuthPage.vue` handles login + register with a tab toggle
- Token + username stored in `localStorage` (`smartdesk_token`, `smartdesk_username`)
- Axios request interceptor attaches `Authorization: Bearer <token>` to every call
- Axios response interceptor: on 401 → `clearAuth()` + `window.location.reload()`
- `fetch()` (SSE stream) manually reads token from `getToken()`
- `App.vue` checks localStorage on mount; shows `AuthPage` or main app accordingly
- Logout clears localStorage and resets all reactive state (no page reload needed)

### RAG Pipeline (chat.py → POST /api/chat/stream)

The endpoint now calls `route(query)` first and dispatches to one of three paths:

- **direct**: plain conversational reply via `llm.client.stream()`, no retrieval
- **rag**: existing v1 chain (unchanged — classify → retrieve → optional web search → stream)
- **agent**: `run_agent()` loop → status SSE frames during tool calls → `llm.client.stream()` for final answer

v1 rag chain detail:
1. Validate KB exists, message non-empty
2. Fetch last 5 conversations (multi-turn memory)
3. Query ChromaDB → top-5 chunks **with cosine distances**
4. `_classify(message)` → `conversational` / `meta` / `followup` / `question`
5. `assess_rag_quality(results)` — if best cosine distance ≥ 0.8, RAG is insufficient
6. If insufficient → weather query? → `fetch_weather()` via wttr.in; else `web_search()` via ddgs
7. Build prompt with `_build_prompt(question, context, history, web_results, msg_type)`
8. Stream Gemini response via SSE
9. Detect `[SOURCE_USED]` / `[WEB_USED]` markers in accumulated response
10. Send typed sources JSON: `{sources: [{type, ...}, ...]}`
11. Strip markers, save clean answer to DB
12. Send `[DONE]`

### SSE Frame Types

```json
"text chunk"                                          // plain string → onChunk
{"sources": [{"type": "document"|"web", ...}]}        // sources → onSources
{"status": "Searching knowledge base…"}               // agent progress → onStatus
[DONE]                                                // stream complete → onDone
```

### Gemini Client (gemini_client.py)

- `_find_model()` — auto-discovers first model supporting `generateContent`, cached
- `_build_prompt(question, context, history, web_results, msg_type)` — 4 branches by `msg_type`:
  - `conversational` — brief natural reply, no RAG
  - `meta` — apply format/language instruction to last answer in history
  - `followup` — answer using history + doc context
  - `question` — full RAG: docs + optional web results
- `generate_answer_stream()` — SSE streaming generator
- `generate_answer()` — blocking, used for summaries
- `generate_summary(text)` — summarizes first 4000 chars in 3-5 sentences

### LLM Client (llm/client.py)

Thin Gemini REST wrapper used by the agent layer:
- `complete(messages, tools, system, temperature)` — non-streaming, returns `LLMResponse`
- `stream(messages, system)` — streaming generator, yields text chunks
- `LLMResponse(text, tool_calls, raw)` — parsed response type

### Agent Loop (agent/loop.py)

Think-act loop that yields `AgentEvent` objects:
- `tool_call` — model requested a tool; SSE layer converts to a status frame
- `tool_result` — tool returned; evidence accumulated into `state.evidence`
- `final` — model produced a text answer; includes `messages` for re-streaming

### Document Summary Flow (knowledge_base.py)

Upload endpoint uses FastAPI `BackgroundTasks`:
1. Upload response returns immediately after ChromaDB indexing
2. Background task `_generate_and_store_summary(file_id, text)` opens its own `SessionLocal()` (cannot reuse request session), calls `generate_summary()`, writes result to `uploaded_files.summary`

### ChromaDB (chroma_client.py)

- Collection per KB: `kb_{kb_id}`
- DefaultEmbeddingFunction (ONNX, local, no external calls)
- Chunk size: 800 chars, overlap: 100 chars
- Prefers paragraph (`\n\n`) > sentence (`。.！？`) boundaries
- `query_documents()` returns `[{text, filename, chunk_index, distance}]`; `distance` is cosine distance [0, 2], lower = more relevant

### Message Classification (chat.py)

`_classify(message)` returns one of four types before any RAG work:
- `conversational` — greetings / acknowledgments → skip RAG entirely
- `meta` — format or language instruction → re-run last answer with instruction
- `followup` — references prior context → use last question as RAG query
- `question` — normal new question → full RAG pipeline

**Important:** `\b` word boundaries don't work with Chinese characters in Python regex. Chinese patterns are listed separately without `\b` anchors.

### Tools (tools.py)

```python
RELEVANCE_THRESHOLD = 0.8  # cosine distance; lower = more relevant
assess_rag_quality(results) -> bool
is_weather_query(message) -> bool
fetch_weather(message) -> Optional[str]   # wttr.in JSON API
web_search(query, num_results=5) -> list[dict]  # ddgs; fails silently → []
```

---

## Frontend Architecture

### ChatWindow.vue

- `messages` — `[{id, question, answer, sources, streaming, statusText}]`
- `"document"` sources → 📄 card (gray); `"web"` sources → 🌐 card (green, clickable)
- File chips: click filename → amber summary panel; shows "generating…" if still null
- SSE chunks arrive as `data: "text"`, sources as `data: {"sources":[...]}`, agent progress as `data: {"status":"..."}`; blinking cursor `▋` while `streaming === true`

### API Client (api/index.js)

- Axios for all REST calls; raw `fetch()` for SSE (Axios doesn't support streaming)
- `sendMessageStream(kbId, message, onChunk, onSources, onDone, onStatus)`

---

## Docker Setup

| File | Purpose |
|---|---|
| `docker-compose.yml` | Two services: `backend` (FastAPI) + `frontend` (nginx) |
| `backend/Dockerfile` | python:3.11-slim, installs deps, runs uvicorn on 0.0.0.0:8000 |
| `frontend/Dockerfile` | Multi-stage: node:20-alpine builds → nginx:alpine serves |
| `frontend/nginx.conf` | Serves SPA, proxies `/api/` to `http://backend:8000` |

**Critical nginx settings:** `proxy_buffering off` and `X-Accel-Buffering no` on the `/api/` block — required for SSE streaming to work through nginx.

**Data persistence:** named volume `smartdesk_data` at `/app/data` — both SQLite and ChromaDB write here, survives container restarts.

---

## Environment

Root `.env` (Docker) / `backend/.env` (local dev):
```
GEMINI_API_KEY=your_key_here
SECRET_KEY=your_secret_key_here   # optional; defaults to dev placeholder, must change in prod
```
