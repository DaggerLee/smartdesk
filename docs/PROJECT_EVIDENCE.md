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

## EV-004 — HITL write-note local real-model closure

**Problem:** Deterministic mocks could not prove that the configured Gemini model would route an explicit persistence request to the agent and emit a protocol-valid `write_note` function call.

**Delivered:** A graph-only, API-driven approval flow now pauses before side effects, accepts a strict structured approval, writes one per-user Markdown file atomically, verifies it by reading it back, and derives the delivered and persisted answer only from the committed receipt.

**Evidence:**

- Deterministic backend suite: 243 tests passed; frontend terminal handling: 5 tests passed; Vite production build: 73 modules transformed.
- Real smoke run `task10-7f977ca0f9b5` used the preregistered Chinese persistence query with LangGraph and HITL enabled.
- The live request sequence was one `ListModels` request and exactly two `generateContent` requests: router plus tool proposal. No retry or post-receipt model call occurred.
- The router selected `agent`; the graph emitted `PAUSED` before any Markdown file existed; structured `approve` resumed the stable action ID.
- The receipt reported `succeeded` and read-back verification. The published file was 72 bytes and its independently measured SHA-256 matched the receipt.
- The canonical receipt answer was byte-identical to the single persisted Conversation answer.

**Limitations:** This is one stochastic real-model smoke, not a three-run evaluation. Token and monetary cost are unknown. Docker was unavailable in the active WSL environment, so the run used an isolated local `backend/data/task10-smoke/` root with the same application path semantics; Docker volume/runtime behavior and browser UX remain unverified. Production defaults remain legacy with HITL disabled.
