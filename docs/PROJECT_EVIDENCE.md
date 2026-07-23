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
