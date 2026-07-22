# Verified Agent Delivery Design

## Context

The LangGraph agent currently checks `state["answer"]` for groundedness, but the HTTP layer discards that text, performs a second `llm_stream(final_messages)` generation, sends the regenerated text through SSE, and persists it. The checked object therefore differs from the delivered object, and every affected request pays for one additional generation.

The current checker also has distinct outcomes that are collapsed into a boolean: no evidence is treated as supported, judge parse failures fail open, and max-turn wrap-up skips the judge. The delivery layer must not describe all of these states as verified.

The migration audit also found an independent zero-tool defect: when an agent-routed model returns text without any tool call, `llm_node` stores `answer` but not the model turn in `messages`. The flag-off compatibility path then has no messages to regenerate from and persists an empty answer. This defect must be fixed at the graph-state source rather than relying on verified delivery being enabled.

## Goal

Behind a default-off feature flag, make the LangGraph agent route select one canonical payload deterministically. Any payload emitted by a successfully running enabled route must already be committed as the identical `Conversation.answer`, and no post-graph model generation may occur.

Regardless of the verified-delivery flag, every text exit from `llm_node` must preserve its model turn so the compatibility path cannot silently replace a valid zero-tool answer with an empty payload.

This is a one-way delivery guarantee: emitted implies persisted. A client can disconnect after the commit but before receiving the SSE frame, leaving a persisted answer the client did not observe. Bidirectional exactly-once delivery would require client acknowledgement and idempotent replay, which are outside this experiment.

## Non-goals

- Changing the legacy agent route.
- Adding a generic safety framework, router, advisor, or acknowledgement protocol.
- Adding a new business-database schema solely for delivery status.
- Claiming that no-evidence answers are groundedness-verified.
- Implementing draft streaming with later replacement or retraction.
- Changing the groundedness prompt or the one-revision limit.

## Considered approaches

### 1. Remove only the second generation

Deliver `final_state["answer"]` directly. This is the smallest diff, but it still collapses max-turn, no-evidence, checker-error, and rejected answers into ambiguous states. Rejected because it fixes payload identity without defining failure behavior.

### 2. Explicit verification status and deterministic delivery — selected

Preserve a structured verification status in graph state, then let a small delivery-policy module select either the graph answer or a shared fixed notice. This makes the quality boundary observable and testable while remaining limited to one route and one feature flag.

### 3. Stream an unverified draft and replace it after checking

This improves perceived latency but requires a new frontend protocol and lets users act on text that may later be withdrawn. Rejected for the first experiment because its coordination cost is unrelated to the integrity defect.

## Verification status model

`GraphState` gains `verification_status` with exactly these values:

| Status | Meaning | Delivery policy when enabled |
|---|---|---|
| `verified` | Evidence existed; the judge completed and accepted the final or revised answer. | Deliver the graph answer. |
| `not_applicable` | No evidence existed, so groundedness was not evaluated. | Deliver the graph answer, but trace it as not applicable rather than verified. |
| `unchecked_max_turns` | The safety cap produced a wrap-up answer and skipped the judge. | Deliver the retryable notice. |
| `check_error` | Judge execution or result parsing failed. | Deliver the retryable notice. |
| `rejected` | Unsupported content remained after the permitted revision. | Deliver the non-retry notice. |

## Shared delivery policy

A new focused module owns the status vocabulary, allowed statuses, notice constants, history exclusion values, and payload selection. Tests and the HTTP route import from this single source.

```text
RETRYABLE_VERIFICATION_NOTICE =
  "I couldn't complete a verifiable answer this time. Please try again."

UNSUPPORTED_ANSWER_NOTICE =
  "I couldn't provide an answer that was sufficiently supported by the available evidence."
```

`check_error` and `unchecked_max_turns` use the retryable notice: both are incomplete execution outcomes where another run may succeed, although success is not promised. `rejected`, missing status, and unknown status use the non-retry notice. The second message does not imply that repeating the same request will help.

Both notices are deterministic system messages, not model answers. They are committed and then emitted so history contains exactly what a successfully connected user receives.

## Data flow

1. `groundedness.check()` returns its existing fields plus an explicit status. No evidence returns `not_applicable`; a judge exception or parse failure returns `check_error`; a valid judge result returns `verified` or `rejected`.
2. `groundedness_node` preserves the current single revision. A first `rejected` result requests a revision; the rechecked result becomes the final status. `check_error` and `not_applicable` do not trigger a rewrite.
3. Every ordinary text exit from `llm_node` appends the model turn to `messages`; the max-turn branch sets `unchecked_max_turns` before ending the graph. This zero-tool fix applies under both flag values.
4. With `SMARTDESK_VERIFIED_AGENT_DELIVERY` disabled, the existing two-stage generation and delivery remain unchanged except for the zero-tool state-completeness fix, allowing a controlled comparison. Verification status is still calculated and observed.
5. With the flag enabled, the shared policy selects one canonical payload:
   - `verified` or `not_applicable`: `final_state["answer"]`;
   - `check_error` or `unchecked_max_turns`: the retryable notice;
   - all other, missing, or unknown statuses: the non-retry notice.
6. The route opens its delivery database session, persists the canonical payload, and commits.
7. Only after a successful commit, the route emits the canonical payload as one SSE string frame and then emits `[DONE]`. It makes no post-graph `llm_stream` call.
8. If commit fails, no answer frame is emitted. The generator fails through the existing request error path.

One SSE frame is intentionally used instead of artificial token-sized chunks. It preserves Unicode and whitespace exactly after JSON decoding and does not pretend to provide true model streaming. Existing tool/status frames provide progress before the final payload.

## History hygiene

The verification status is graph state, not a `Conversation` column, so a later database query cannot filter directly on it without a schema migration. Instead, both shared notice constants form `NON_CONTEXT_ANSWERS`.

Every query that constructs multi-turn history excludes rows whose `Conversation.answer` exactly matches a value in `NON_CONTEXT_ANSWERS` before ordering and applying `limit(5)`. The notices remain visible in user history, but they are not fed back as prior assistant content and do not displace one of the five usable turns.

This exact-string filter is intentionally narrow. Once a notice is released, its literal is frozen. If user-facing wording must change, the old literal remains in the append-only `NON_CONTEXT_ANSWERS` set and the new literal is added; old entries are never edited or removed. A future user-visible delivery-status feature should replace this mechanism with an explicit database column and migration.

## Feature flag and compatibility

`SMARTDESK_VERIFIED_AGENT_DELIVERY` defaults to false. Only the LangGraph `route == "agent"` delivery branch changes behavior when enabled. Direct, RAG, and legacy agent delivery remain unchanged.

Unknown or absent verification status fails closed to the non-retry notice when the flag is enabled. This prevents older or malformed graph state from being silently treated as verified.

## Failure-analysis evidence and privacy

The raw blocked model answer is never written into the user-visible `Conversation` row or duplicated into the JSONL trace. The existing LangGraph checkpointer already persists the full graph state, including the raw answer, under the per-request `thread_id`.

The delivery trace includes `thread_id`, allowing a developer to retrieve the blocked state from the local checkpoint database for false-rejection analysis. Runtime checkpoint directories at both `backend/data/` and repository-root `data/` must be gitignored. Checkpoint data is local diagnostic data, may contain user or retrieved content, and must never be committed or copied into CASE-002. Retention cleanup is a separate operational concern and is recorded as a threat to validity rather than silently claiming indefinite safe storage.

## Observability and measurement

An `agent_delivery` trace is written for both flag states after persistence succeeds. It contains:

- `thread_id`;
- `verification_status`;
- `feature_enabled`;
- `delivery_kind` (`graph_answer`, `retryable_notice`, `unsupported_notice`, or `regenerated_answer`);
- SHA-256 of the graph answer;
- SHA-256 of the persisted payload;
- whether those hashes match;
- `post_graph_generation_calls` (zero when enabled, one for the existing flag-off agent path);
- persisted payload character count.

The trace describes committed server state, not client receipt. No event may claim that the client received the payload without a client acknowledgement.

Before/after comparison uses equivalent LangGraph agent requests and records:

- model generation calls per request;
- completion latency and time to first visible event;
- graph-answer/persisted-payload match count for statuses that should deliver the graph answer;
- verification-status distribution;
- fixed-notice rate by status;
- output characters as the currently available cost proxy.

Flag-off runs record status distribution before rollout, allowing the owner to estimate the enabled path's notice rate before exposing it.

The Gemini client does not currently expose token or monetary usage. Exact tokens and cost remain `unknown`; they must not be reported as zero. The experiment can still prove that one generation call was removed.

## Eval alignment

The existing agent gold set supplies real queries for flag-off and flag-on status-distribution comparison. `ItemResult`, per-item JSONL, aggregate output, and `results/history.jsonl` gain verification-status data when the LangGraph backend is used.

Eval output also records the answer's scope. `production_delivered` is reserved for LangGraph with verified delivery enabled, where the shared delivery policy selects the exact payload scored by quality metrics. Agent runs on legacy or with verified delivery disabled are labeled `agent_internal`; their quality metrics describe the graph/loop answer, not the text a user necessarily received. Direct and RAG harness answers are labeled `eval_simplified` because those runners do not execute the production orchestration. Aggregate and history records include answer-scope and delivery-kind distributions.

Natural gold queries are not required to force `check_error` or `unchecked_max_turns`: external API failure and turn exhaustion are not stable semantic properties of a question. Those statuses use deterministic fault-injection integration tests. The existing gold set remains the end-to-end quality comparison and receives a changelog entry for the new recorded field; it is not padded with unstable queries merely to obtain nominal status coverage.

## Error handling

- No raw unchecked answer is sent or stored in `Conversation` for `unchecked_max_turns`, `check_error`, `rejected`, or unknown status when the flag is enabled.
- A database commit occurs before the answer frame. A commit failure emits no answer frame and writes no success trace.
- A disconnect after commit but before receipt can leave a persisted unseen answer. This is explicitly outside the one-way guarantee and is measured as a protocol limitation, not described as exactly-once delivery.
- Retry notices are excluded from future model context by shared constant identity.
- Client-side acknowledgement and replay are not added in this experiment.

## Test strategy

Tests are written first and must fail for the expected missing behavior before production changes.

1. Groundedness result tests cover `verified`, `rejected`, `not_applicable`, and `check_error` without network calls.
2. Graph tests cover normal acceptance, revision then acceptance, rejection after one revision, no evidence, checker error, and max-turn status.
   They also cover a zero-tool agent response and require its model turn to remain available for delivery.
3. Delivery-policy tests cover both allowed statuses, each blocked-status mapping, missing/unknown status, and shared history-exclusion constants.
4. Router tests enable the feature flag and assert:
   - the database commit occurs before the first answer frame;
   - graph answer equals reconstructed SSE answer equals persisted answer for `verified` and `not_applicable`;
   - blocked statuses and unknown status produce and persist only their shared notice;
   - commit failure emits no answer frame;
   - no post-graph `llm_stream` call occurs;
   - direct, RAG, legacy, and flag-disabled behavior remain unchanged.
   - a real zero-tool LangGraph run delivers a non-empty answer under both flag values.
5. History tests assert both notices remain stored but are excluded before the five-row usable-history limit.
6. Trace tests assert both flag states record status and hashes only after successful persistence, with no raw blocked answer.
7. Eval tests assert LangGraph results and archived aggregates include verification-status distribution.
   They also assert that enabled LangGraph quality metrics score the shared delivery-policy payload, while legacy/flag-off Agent and simplified direct/RAG results are explicitly labeled with non-delivery scopes.
8. Existing gold-set runs compare quality and notice-rate distributions; deterministic fault tests, not natural queries, cover `check_error` and max-turn exhaustion.

## Acceptance criteria

- Feature flag defaults off.
- A zero-tool agent response preserves its model turn and cannot become an empty delivered answer under either flag value.
- With the flag on, the LangGraph agent performs zero model generations after graph completion.
- No answer SSE frame is emitted before its identical payload is committed.
- For every completed enabled-path request, reconstructed SSE text equals the stored `Conversation.answer` exactly.
- `verified` and `not_applicable` deliver the graph answer.
- `check_error` and `unchecked_max_turns` deliver the shared retryable notice.
- `rejected`, missing status, and unknown status deliver the shared non-retry notice.
- Both notices remain visible in history but are excluded from future model context.
- Revision output is checked again before it can receive `verified`.
- Raw blocked answers remain recoverable through local checkpoint state linked by `thread_id`, and runtime checkpoint paths are ignored by git.
- Flag-off traces and eval archives record status distribution before rollout.
- Eval archives distinguish `production_delivered`, `agent_internal`, and `eval_simplified`; only the first may be described as user-visible answer quality.
- Existing direct, RAG, legacy-agent, checkpointing, graph self-healing, and eval tests remain green.
- CASE-002 records the implementation commit, fresh verification output, known latency/call-count measurements, and unavailable cost fields as `unknown`.

## Rollout

Keep the flag off by default. First collect status distribution and baseline call/latency data with the flag off. Run the focused test suite, then the same committed gold set with the flag off and on. Adopt the enabled path only if emitted-without-persistence remains zero, graph-answer delivery mismatch falls to zero for allowed statuses, the extra generation disappears, regressions remain absent, and the notice rate is acceptable to the human owner.

The real-request smoke comparison is exactly five requests: one query run once with the flag off and once with it on, plus three distinct queries run only with the flag on. The off/on latency comparison is therefore **`n=1 paired`**. It is recorded as a single-sample indicative value, not a statistical conclusion. The post-graph generation-call change from one to zero is deterministic instrumentation evidence and is reported separately from latency.

## Change control

This design is evidence-controlled, not immutable.

- If a failing test reveals a technical contradiction, pause implementation, update this design and the implementation plan in a dedicated commit, then resume TDD.
- Clarifications that do not change user-visible behavior, risk tolerance, goals, or evaluation criteria may be corrected immediately with the reason recorded.
- Changes to delivery policy, privacy, rollout gates, goals, or evaluation criteria require human approval.
- Post-run defects and measurements are recorded in CASE-002 before superseding any decision. Historical decisions remain visible and are marked as superseded rather than silently rewritten.
- Governance changes (`AGENTS.md`, project naming, and operating rules) use commits separate from feature implementation so a feature rollback does not revert governance with it.
