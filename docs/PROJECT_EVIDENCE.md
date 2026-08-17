# SmartDesk Project Evidence

This append-only log records verified engineering outcomes that can be traced to code, tests, eval artifacts, or runtime evidence. It deliberately excludes plans and unverified completion claims.

## EV-001 — Measurable agent baseline

**Problem:** Agent changes could not be judged objectively without a stable evaluation contract.

**Delivered:** A 35-item gold set and evaluation harness covering routing, retrieval, answer keywords, groundedness, faithfulness, and answer relevancy.

**Evidence:**

- Baseline commit: `e8dddab`
- Harness commit: `a6c2acc`
- Recorded baseline: router accuracy 91.7%, end-to-end contains pass 94.4%, grounded rate 88%, zero execution errors.
- Later safeguards added run locking, resume, tracked-dirty-tree rejection, per-run archives, and same-period baseline comparison.

**Limitations:** LLM metrics are stochastic. Formal comparisons require contemporary three-run means; historical pooled numbers are trend context only.

## EV-002 — LangGraph migration with crash recovery

**Problem:** The hand-written loop could not provide durable pause/resume semantics needed for human approval workflows.

**Delivered:** The router and agent workflow were migrated into explicit LangGraph nodes, connected to SSE, and backed by SQLite checkpoints with synchronous durability.

**Evidence:**

- Graph skeleton: `98fa5a1`
- Explicit agent nodes: `ecafc3f`
- SSE integration: `e257dfd`
- Checkpointer and resume foundation: `f20b3ce`
- A real `kill -9` test verified that committed graph steps survived process death and resume did not replay completed classify/tool steps.
- Same-commit, same-period three-run comparison found legacy and graph metrics within the registered noise band.

**Limitations:** Resume is not yet exposed through an HTTP endpoint, and SQLiteSaver is suitable for a single-process/demo deployment rather than multi-worker scale.

## EV-003 — Verified agent answer delivery

**Problem:** The agent checked one answer for evidence support, then regenerated a different answer for SSE delivery, so checked, persisted, and user-visible text could diverge.

**Delivered:** An optional delivery policy makes the finalized graph answer the single payload, commits it before emitting SSE, excludes fallback notices from future model context, and records explicit verification and answer-scope states.

**Evidence:**

- Delivery implementation: `8ea72dc`
- Verification/answer-scope eval recording: `11d7cf6`
- Zero-tool empty-answer regression fix: `7ca4cc5`
- Real SSE trace-context repair: `73944d9`
- Checkpoint history serialization repair: `745bdbb`
- Milestone merge: `6a38620`
- Main passed 95 tests after merge.
- One paired rollout showed post-graph answer generation decrease from one call to zero. Its latency comparison is a single paired observation, not a statistical result.

**Limitations:** The feature flag remains off. The initial empty-KB rollout produced fallback notices in three of four enabled requests; a populated-KB diagnostic produced two verified answers, one max-turn fallback, and one evidence rejection. Exact token cost is unknown.

## EV-004 — HITL write-note real-model closure

**Problem:** Deterministic mocks could not prove that the configured Gemini model would route an explicit persistence request to the agent and emit a protocol-valid `write_note` function call.

**Delivered:** A graph-only, API-driven approval flow now pauses before side effects, accepts a strict structured approval, writes one per-user Markdown file atomically, verifies it by reading it back, and derives the delivered and persisted answer only from the committed receipt.

**Evidence:**

- Deterministic backend suite: 243 tests passed; frontend terminal handling: 5 tests passed; Vite production build: 73 modules transformed.
- Real smoke run `task10-7f977ca0f9b5` used the preregistered Chinese persistence query with LangGraph and HITL enabled.
- The live request sequence was one `ListModels` request and exactly two `generateContent` requests: router plus tool proposal. No retry or post-receipt model call occurred.
- The router selected `agent`; the graph emitted `PAUSED` before any Markdown file existed; structured `approve` resumed the stable action ID.
- The receipt reported `succeeded` and read-back verification. The published file was 72 bytes and its independently measured SHA-256 matched the receipt.
- The canonical receipt answer was byte-identical to the single persisted Conversation answer.
- Docker run `task10-181d4f7e5b84` used the same preregistered query and request budget after the safe protocol diagnostics commit `fbc992c`.
- The Docker request sequence was one `ListModels` plus exactly two `generateContent` requests, with no retry or post-receipt Gemini call.
- The terminal checkpoint recorded `succeeded`; the per-user volume file was 72 bytes and its independently reread SHA-256 matched the receipt.
- An API-only idempotent resolve read back the committed receipt without a model call and returned HTTP 200, `succeeded`, the canonical answer, and `[DONE]`; checkpoint, delivered answer, and Conversation were identical.

**Limitations:** These are one local and one Docker stochastic real-model success, not a three-run evaluation. Token and monetary cost are unknown. Two earlier bounded Docker attempts returned HTTP 200 without usable router candidate content and stopped before proposal with zero file writes. The successful Docker run's first verifier incorrectly omitted the server-owned `users/{user_id}` directory when locating the file and therefore recorded a harness failure; the preserved checkpoint, file, receipt, Conversation, and idempotent SSE readback independently verify product closure. Browser UX remains unverified, and production defaults remain legacy with HITL disabled.

## EV-005 — HITL write-note production cutover

**Problem:** The real-model closure in EV-004 proved the write protocol, but
the production defaults were still legacy/HITL-off and the browser had not
visually demonstrated distinct paused and failed terminal states.

**Delivered:** The API-only HITL workflow is now the default LangGraph path.
Ordinary graph conversations persist by their generated thread identity,
backend selection has one strict configuration owner for chat and eval, and
the frontend treats `[PAUSED]` and `[FAILED]` as distinct non-answer terminals
without adding approval controls.

**Evidence:**

- Milestone PR: `#1`; merge commit: `d4f9784`.
- Pre-cutover corrections: Conversation persistence `825bc2d`; centralized
  strict backend configuration `009d96d`; independent default cutover
  `ad46629`.
- Fresh post-merge verification on `main@d4f9784`: 258 backend tests passed
  with 7 existing SQLAlchemy deprecation warnings; 5 frontend tests passed;
  the Vite production build transformed 73 modules.
- Human Compose validation ran `docker compose config -q` from the SmartDesk
  repository and returned exit code 0. No container or model request was
  started by that check.
- Human browser attestation used the real SmartDesk frontend with a temporary
  zero-Gemini mock API: paused and failed outcomes both stopped loading,
  displayed distinct states, did not render their terminal markers as answer
  text, and introduced no approval UI.
- The earlier local run `task10-7f977ca0f9b5` and Docker run
  `task10-181d4f7e5b84` each completed router, function call, interrupt,
  approval, receipt, Conversation, and final delivery using one `ListModels`
  plus exactly two `generateContent` requests and no retry or post-receipt
  model call.
- The verified Markdown artifact was 72 bytes. Its independent SHA-256
  measurement,
  `a03c90e300fced691ebe7b71dbba9e54ac80bf06a1c37f007fb73dab755d1536`,
  matched the committed receipt.

**Limitations:** The live evidence is one local and one Docker stochastic
success, not a three-run evaluation; token and monetary cost remain unknown.
Browser acceptance used a deterministic zero-Gemini API and therefore proves
client terminal behavior, not another live model round trip. Approval remains
API-only, and the SQLite checkpointer remains a single-process/demo
constraint.

## EV-006 — Unified Markdown XSS boundary

**Problem:** Human browser testing showed that executable HTML in an AI answer
could pass through Markdown rendering into Vue's `v-html` sink and execute in
the real frontend.

**Delivered:** Historical and streaming AI answers now share one frontend
rendering boundary: Markdown is converted to HTML, sanitized with DOMPurify,
and only then passed to the sole `v-html` sink. Source-level tests prevent a
future direct-answer render or duplicate parser/sink from bypassing that
boundary.

**Evidence:**

- Human reproduction on 2026-07-23 set a browser-side probe through an
  `onerror` handler, confirming executable HTML rather than a scanner-only
  warning.
- Core correction: `56d4fc3`; boundary enforcement: `9d7e889`; rendering
  contract coverage: `29bac52`.
- PR #3 merged the work into `main` at `1a026c4`.
- All 11 frontend tests passed after the final correction.
- The production build completed with 75 transformed modules, and the
  test-only jsdom dependency was absent from the production bundle.
- The test suite preserves headings, emphasis, lists, fenced code, and safe
  links while removing executable HTML, event handlers, and unsafe URL
  protocols.
- No Gemini request was required for reproduction, implementation, or final
  deterministic verification.

**Limitations:** This is a focused rendering-boundary correction, not a full
content-security-policy rollout or a backend sanitization framework. Separate
observations about narrow-screen layout, interrupted SSE state, and paused
flow refresh continuity remain deferred and are not claimed as fixed.

## EV-007 — Browser approval controls

**Problem:** The default HITL write path could pause and resolve through the
API, but the browser exposed only a terminal waiting state. A user could not
approve, edit, or reject the pending `write_note` proposal from the page.

**Delivered:** The current chat page now stores the `confirmation_required`
payload on the paused message, renders one approval card, and resolves through
the existing strict `/api/chat/actions/{thread_id}/resolve` SSE endpoint.
Approve sends no title/content, edit sends a complete replacement title and
content, and reject omits a blank reason. Successful resolve streams clear the
pending controls only after the canonical receipt answer is delivered.

**Evidence:**

- Frozen-spec amendment and acceptance brief: `d8c7cd2`.
- Frontend parser/state verification after implementation: 21 tests passed via
  `node --test src/**/*.test.js`.
- Production build completed with 75 transformed modules via `npm run build`.
- Backend regression suite passed after isolating eval delivery-policy tests
  from local retrieval database state: 258 tests passed via `pytest`.
- Browser smoke used a deterministic zero-Gemini fixture against the real Vite
  frontend in headless Chromium. It selected a knowledge base, submitted two
  chat requests, rendered the approval card, approved the first proposal,
  rejected the second proposal, observed both canonical receipt answers, and
  asserted the resolve payloads were exactly
  `{action_id:"action-1", decision:"approve"}` and
  `{action_id:"action-2", decision:"reject"}`.
- No backend route, schema, model, or Gemini integration changed.

**Limitations:** Approval controls are current-page state only. Refresh
recovery, pending-action lists, Note CRUD/list APIs, and cross-session approval
queues remain outside scope. The browser smoke was deterministic and local; it
does not claim another live-model browser run or paid Gemini request.
