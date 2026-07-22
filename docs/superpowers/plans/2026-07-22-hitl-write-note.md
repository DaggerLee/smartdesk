# HITL Write Note Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a graph-only, API-driven `write_note` approval loop that writes one verified per-user Markdown file at most once while leaving Phase A production defaults unchanged.

**Architecture:** A shared high-precision write-intent classifier gates tool exposure. LangGraph checkpoints a proposal before `approval_gate` interrupts, resumes with a strict `Command`, publishes the file atomically, checkpoints a redacted receipt, and uses `action_finalize_node` to deliver a deterministic receipt-derived answer without another Gemini call. The legacy backend changes behavior only when the independent HITL flag is enabled, and global LangGraph cutover is a later independent commit.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, SQLAlchemy/SQLite, LangGraph `SqliteSaver`, pytest, Vue 3/Vite, Node's built-in test runner where practical.

## Global Constraints

- Phase A defaults remain `SMARTDESK_AGENT_BACKEND=legacy` and `SMARTDESK_HITL_WRITE_NOTE=false`.
- No real Gemini call before notifying the user of exact scope and expected request count.
- No implementation commit may include the final backend-default cutover.
- `SYSTEM_PROMPT` remains the shared base; graph appends `WRITE_NOTE_POLICY` without copying the base prompt.
- Action statuses are exactly `proposed`, `approved`, `rejected`, `succeeded`, `replayed`, `conflict`, and `failed`.
- Write terminal states bypass groundedness and use `verification_status="verified"`, `verification_source="action_receipt"` only after deterministic receipt verification.
- User-visible result templates omit SHA-256, byte count, and safe error code.
- Do not stage or modify unrelated eval result artifacts.
- Every production behavior starts with a test that is observed failing for the expected missing behavior.

---

### Task 1: Shared feature policy and write-intent shadow replay

**Files:**
- Create: `backend/agent/write_note_policy.py`
- Create: `backend/scripts/shadow_write_intent.py`
- Create: `backend/tests/test_write_note_policy.py`
- Create: `backend/tests/test_shadow_write_intent.py`
- Modify: `backend/config.py`

**Interfaces:**
- Produces: `WriteIntent = Literal["none", "draft", "persist"]`.
- Produces: `classify_write_intent(text: str) -> WriteIntent`.
- Produces: `is_hitl_write_note_enabled() -> bool`.
- Produces frozen `LEGACY_WRITE_UNAVAILABLE_NOTICE`, `WRITE_NOTE_POLICY`, and bounded input constants.

- [ ] Write parameterized failing tests covering English/Chinese explicit saves, explicit draft/negation, and near negatives (`record this`, `remember this`, `note that`, `记录一下`, `记住这个`).
- [ ] Run `cd backend && pytest -q tests/test_write_note_policy.py` and confirm failure because the shared policy module does not exist.
- [ ] Implement the smallest high-precision classifier with separate Chinese patterns and no Chinese `\b` usage; add the default-off feature reader and policy constants.
- [ ] Re-run the focused tests and confirm they pass.
- [ ] Write failing tests for a read-only shadow replay that accepts supplied Conversation/eval text, returns only `persist`/`draft` candidates, and never reports precision without adjudicated labels.
- [ ] Implement the script with dependency-injected readers and JSON output; it must not open a write transaction.
- [ ] Run the shadow replay over current eval queries and the current local Conversation database when present; save output only as an untracked scratch artifact and report candidate counts without accuracy claims.
- [ ] Run `pytest -q tests/test_write_note_policy.py tests/test_shadow_write_intent.py`.
- [ ] Commit only Task 1 files with `feat(agent): add guarded write intent policy`.

### Task 2: Receipt types, strict schemas, and deterministic final templates

**Files:**
- Create: `backend/agent/write_action.py`
- Create: `backend/tests/test_write_action.py`

**Interfaces:**
- Produces: `WriteNotePayload`, `ActionReceipt`, `PendingAction`, and `ActionResolution` typed structures.
- Produces: strict Pydantic resolution models used by the route.
- Produces: `validate_write_note_payload(title: str, content: str) -> WriteNotePayload`.
- Produces: `render_action_answer(receipt: ActionReceipt, language: str) -> str`.
- Produces: `to_action_evidence(receipt: ActionReceipt) -> dict` with no note content.

- [ ] Write failing tests for exact status vocabulary, `extra="forbid"`, approve/edit/reject field rules, and title/content/reason limits from the spec.
- [ ] Run `pytest -q tests/test_write_action.py` and observe the expected import/behavior failures.
- [ ] Implement only the typed models and validators required by those tests.
- [ ] Add failing tests for succeeded/replayed/rejected/conflict/failed templates, asserting no user template includes hash, byte count, or error code.
- [ ] Implement deterministic English/Chinese templates and typed redacted action evidence.
- [ ] Add tests proving original and approved payloads are distinct values and edit cannot overwrite the proposal.
- [ ] Run the focused file and commit with `feat(agent): define write action contract`.

### Task 3: Atomic per-user Markdown writer

**Files:**
- Create: `backend/agent/tools/write_note.py`
- Create: `backend/tests/test_write_note_tool.py`
- Modify: `backend/config.py`

**Interfaces:**
- Produces: `WriteNoteTool(user_id: int, action_id: str, notes_root: Path)`.
- Produces: `run(title: str, content: str) -> ActionReceipt`.
- Consumes payload validation and receipt types from Task 2.

- [ ] Write failing tests for canonical Markdown bytes, server-generated relative filename, per-user isolation, and read-back verified `succeeded` receipt.
- [ ] Run the focused test and confirm the writer is missing.
- [ ] Implement validation, safe slug generation, temporary same-directory write, fsync, atomic no-clobber publication, and final read-back.
- [ ] Add failing tests for identical-file replay, different-file conflict, target symlink, parent symlink, path separators, and reject creating no directory (reject remains outside the tool).
- [ ] Implement directory-FD/no-follow checks and exact existing-file comparison without overwrite.
- [ ] Add a failing crash-window/concurrency test showing only one publish succeeds and the other call returns replayed.
- [ ] Implement the minimal safe race handling.
- [ ] Run `pytest -q tests/test_write_note_tool.py` and commit with `feat(agent): add atomic write note tool`.

### Task 4: Graph proposal, interrupt, bounded invalid rounds, and receipt finalization

**Files:**
- Modify: `backend/agent/graph.py`
- Modify: `backend/agent/loop.py` only to expose shared `_MAX_TOOL_FAILURES` semantics without copying values.
- Create: `backend/tests/test_graph_hitl_write.py`

**Interfaces:**
- Consumes feature policy, action contract, and `WriteNoteTool`.
- Produces graph events `confirmation_required` and checkpoint-derived `action_result`.
- Produces `resume_graph_action(thread_id: str, resolution: ActionResolution, ...)` or an equivalent single graph execution entry point using `Command(resume=...)`.

- [ ] Write a failing graph test proving a sole write call creates one stable action ID, commits a proposal, interrupts before tool execution, and leaves the filesystem untouched.
- [ ] Run the test and observe failure because write topology is absent.
- [ ] Add `user_id`, `thread_id`, `write_intent`, `pending_action`, `write_action_seen`, `verification_source`, and invalid-round failure state to `GraphState`; add graph-only tool exposure behind both gates.
- [ ] Add `approval_gate` and edges with no pre-interrupt side effects; make `stream_graph` distinguish paused from final state by rereading the checkpoint.
- [ ] Re-run the first test to green.
- [ ] Write failing tests for approve, full edit, reject, action ID stability, ownership data persistence, and the original/approved split.
- [ ] Implement `Command(resume=...)` resolution and synchronous state transitions.
- [ ] Write failing tests that mixed read/write and multiple-write rounds execute zero tools and share the bounded `_MAX_TOOL_FAILURES` stop policy.
- [ ] Implement whole-round validation before any tool execution.
- [ ] Write failing tests proving succeeded/replayed/rejected/conflict/failed receipts append legal `functionResponse` messages, make zero post-receipt Gemini calls, bypass groundedness, and enter `action_finalize_node`.
- [ ] Implement receipt finalization with `verification_status="verified"` and `verification_source="action_receipt"`; keep ordinary groundedness as `llm_groundedness`.
- [ ] Add flag-on/flag-off tests proving `action_receipt` always bypasses the ordinary verified-delivery two-stage branch, delivers canonical `GraphState.answer`, never selects `llm_stream`, and makes zero post-graph Gemini calls.
- [ ] Add strict trace/evidence whitelist tests proving `title`, `content`, reject `reason`, `original_payload`, and `approved_payload` never appear; permit only approved redacted receipt metadata.
- [ ] Run graph/self-healing/checkpoint suites and commit with `feat(graph): add checkpointed write approval flow`.

### Task 5: Conversation thread migration and idempotent persistence

**Files:**
- Modify: `backend/models.py`
- Modify: `backend/main.py`
- Create: `backend/tests/test_conversation_thread_migration.py`
- Create or modify focused persistence tests in `backend/tests/test_verified_delivery_route.py`.

**Interfaces:**
- Produces nullable `Conversation.thread_id`.
- Produces partial unique index `ix_conversations_thread_id_unique`.
- Produces an idempotent helper that inserts once by thread ID or verifies an identical existing row.

- [ ] Write failing migration tests against an old-schema SQLite database.
- [ ] Implement repeatable column detection and partial unique-index creation; do not swallow arbitrary migration errors as "already exists".
- [ ] Add failing tests for two legacy NULL rows, unique non-NULL thread IDs, identical completion reuse, and conflicting completion rejection.
- [ ] Implement the minimal persistence helper.
- [ ] Run migration and delivery tests and commit with `feat(chat): persist graph conversations by thread`.

### Task 6: Resolve API, authorization, locks, and three SSE boundaries

**Files:**
- Modify: `backend/routers/chat.py`
- Create: `backend/agent/action_locks.py`
- Create: `backend/tests/test_hitl_resolve_route.py`

**Interfaces:**
- Produces `POST /api/chat/actions/{thread_id}/resolve`.
- Consumes strict resolution schemas and graph resume entry point.
- Produces terminal SSE markers `[PAUSED]`, `[FAILED]`, and existing `[DONE]`.

- [ ] Write failing route tests for owner approval, cross-user 404, unknown action 404, strict body validation, and ordinary chat text not resolving an action.
- [ ] Implement a per-thread lock registry and hold the lock across checkpoint load, authorization, reread, resume, action commit, finalization, and Conversation commit.
- [ ] Write failing tests for exact repeated resolution, changed edit payload/reject reason, and conflicting decisions.
- [ ] Implement idempotent stored-result return and 409 conflicts.
- [ ] Write failing SSE ordering tests: confirmation only after proposal checkpoint; action result only after terminal receipt checkpoint; final answer only after Conversation commit.
- [ ] Implement checkpoint rereads before confirmation/action frames; when verification_source is action_receipt, bypass ordinary verified-delivery selection and commit canonical GraphState.answer before emitting it.
- [ ] Test byte-for-byte identity between canonical SSE answer and Conversation.answer with the ordinary verified-delivery flag both enabled and disabled.
- [ ] Add failure tests for proposal, action-result checkpoint, graph finalization, and Conversation commit; assert typed stage error plus `[FAILED]` and absence of later success frames.
- [ ] Add a summarize-and-save test proving the file contains the summary while final chat contains only the canonical receipt template.
- [ ] Run focused route, verified-delivery, checkpoint, and streaming tests; commit with `feat(chat): expose HITL action resolution`.

### Task 7: Legacy emergency rollback fail-closed

**Files:**
- Modify: `backend/routers/chat.py`
- Modify: `backend/agent/delivery.py`
- Create: `backend/tests/test_legacy_write_fallback.py`

**Interfaces:**
- Consumes shared write intent and feature flag.
- Adds frozen `LEGACY_WRITE_UNAVAILABLE_NOTICE` to append-only `NON_CONTEXT_ANSWERS`.

- [ ] Write failing tests for the four flag combinations, proving current defaults remain byte-for-byte unchanged.
- [ ] Write the emergency combination test (`legacy + HITL enabled`) proving explicit persist returns the fixed notice without calling the legacy model, creating a directory, or creating pending state.
- [ ] Implement the pre-agent fail-closed gate and shared notice ownership.
- [ ] Add history tests proving the notice remains visible but is excluded from future context before the five-row limit.
- [ ] Run focused legacy and history tests; commit with `feat(chat): fail closed on legacy write rollback`.

### Task 8: API-only frontend paused/failed handling

**Files:**
- Modify: `frontend/src/api/index.js`
- Modify: `frontend/src/components/ChatWindow.vue`
- Modify: `frontend/package.json` only if a minimal test runner dependency is unavoidable.
- Create: `frontend/src/api/index.test.js` or the smallest equivalent frontend test.

**Interfaces:**
- Extends `sendMessageStream` with terminal callbacks for paused and failed outcomes without adding approval UI.

- [ ] Write failing stream-parser tests proving `[PAUSED]` and `[FAILED]` are not chunks, settle the stream once, and expose distinct terminal outcomes.
- [ ] Implement parser callbacks while preserving natural-end `onDone` behavior.
- [ ] Write a failing component-level test or extract a focused state reducer proving paused ends loading and displays "waiting for confirmation", while failed ends loading with a failure state.
- [ ] Implement the minimal UI state; do not add approve/edit/reject buttons.
- [ ] Run frontend tests and `npm run build`; commit with `feat(frontend): handle paused HITL streams`.

### Task 9: HITL gold protocol, cutover regressions, and governance handoff

**Files:**
- Create: `backend/eval/hitl_gold_set.jsonl`
- Modify: `backend/eval/GOLD_SET_CHANGELOG.md`
- Create: `backend/tests/test_hitl_gold_set.py`
- Create or modify: `backend/tests/test_graph_chat_routes.py`
- Modify at feature boundary: `docs-local/SmartDesk_Decisions.md`
- Modify at feature boundary: `docs-local/CURRENT.md`
- Modify only after verified milestone: `docs/PROJECT_EVIDENCE.md`

**Interfaces:**
- Produces deterministic gold cases required by the frozen spec.
- Produces full HTTP direct/RAG/agent cutover evidence without changing defaults.

- [ ] Add failing schema/coverage tests requiring English/Chinese persist positives, negations, near negatives, approve/edit/reject, invalid rounds, receipt statuses, and summarize-save receipt-only UX.
- [ ] Add the gold JSONL and changelog entry.
- [ ] Write complete graph-backed HTTP route tests for direct SSE/persistence/history and RAG SSE/sources/answer identity/history.
- [ ] Run those tests alongside existing real graph/SSE agent regressions.
- [ ] Run the complete backend suite and frontend suite/build.
- [ ] Update Decisions with `verification_source` meanings, CURRENT with the last verified state and exact smoke entry point, and PROJECT_EVIDENCE only for outcomes actually verified.
- [ ] Commit gold/governance changes separately from feature code using an English docs/eval commit message.

### Task 10: Real Gemini smoke gate — mandatory stop for user notice

**Files:**
- No production file changes before notice.
- Preserve smoke output under the established untracked result/log convention.

**Interfaces:**
- Controlled flags: `SMARTDESK_AGENT_BACKEND=langgraph`, `SMARTDESK_HITL_WRITE_NOTE=true`.

- [ ] Stop and tell the user the exact query, `approve` resolution, feature flags, expected two Gemini requests (router plus tool proposal), paid-quota scope, and that tokens/cost may remain unknown.
- [ ] Do not run the smoke until that notice has been delivered.
- [ ] After authorization/continuation, run one router -> functionCall -> interrupt -> resume -> receipt -> final-delivery smoke against the Docker-persisted data path.
- [ ] Record actual model request count, result receipt, relative path, Conversation identity, and observed cost as `unknown` when unavailable.
- [ ] If the smoke fails, do not cut over defaults; return to a failing deterministic regression test before fixing code.

### Task 11: Independent default cutover commit

**Files:**
- Modify: `backend/config.py`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify/add cutover default tests.

**Interfaces:**
- Changes defaults to LangGraph plus HITL enabled only after Task 10 passes.
- Retains `SMARTDESK_AGENT_BACKEND=legacy` as the emergency rollback.

- [ ] Write failing config tests for final defaults and fail-fast rejection of every backend value other than exactly legacy or langgraph; invalid-backend production code belongs only to this task.
- [ ] Change config, example environment, and Docker defaults only.
- [ ] Run full direct/RAG/agent cutover tests and the complete suite again.
- [ ] Commit as an independent cutover commit; do not merge or push.

## Plan self-review

- Every frozen spec section maps to a task above.
- Phase A defaults and the final cutover are separated.
- The real Gemini step is an explicit user-notice stop.
- No task creates a Note table, approval UI, free-text approval, or legacy write tool.
- Receipt finalization has no post-receipt Gemini completion.
- Prompt, intent rules, notices, status vocabulary, and verification-source meanings each have one owner.
