# Verified Agent Delivery Design

## Context

The LangGraph agent currently checks `state["answer"]` for groundedness, but the HTTP layer discards that text, performs a second `llm_stream(final_messages)` generation, sends the regenerated text through SSE, and persists it. The checked object therefore differs from the delivered object, and every affected request pays for one additional generation.

The current checker also has distinct outcomes that are collapsed into a boolean: no evidence is treated as supported, judge parse failures fail open, and max-turn wrap-up skips the judge. The delivery layer must not describe all of these states as verified.

## Goal

Behind a default-off feature flag, make the LangGraph agent route select one canonical payload deterministically and guarantee that every successfully completed request delivers and persists that payload without any post-graph model generation.

## Non-goals

- Changing the legacy agent route.
- Adding a generic safety framework, router, advisor, or new persistence schema.
- Claiming that no-evidence answers are groundedness-verified.
- Implementing draft streaming with later replacement or retraction.
- Changing the groundedness prompt or the one-revision limit.

## Considered approaches

### 1. Remove only the second generation

Deliver `final_state["answer"]` directly. This is the smallest diff, but it still collapses max-turn, no-evidence, checker-error, and rejected answers into ambiguous states. Rejected because it fixes payload identity without defining failure behavior.

### 2. Explicit verification status and deterministic delivery — selected

Preserve a structured verification status in graph state, then let the HTTP layer choose either the graph answer or a fixed retry notice. This makes the quality boundary observable and testable while remaining limited to one route and one feature flag.

### 3. Stream an unverified draft and replace it after checking

This improves perceived latency but requires a new frontend protocol and lets users act on text that may later be withdrawn. Rejected for the first experiment because its coordination cost is unrelated to the integrity defect.

## Verification status model

`GraphState` gains `verification_status` with exactly these values:

| Status | Meaning | Delivery policy when enabled |
|---|---|---|
| `verified` | Evidence existed; the judge completed and accepted the final or revised answer. | Deliver the graph answer. |
| `not_applicable` | No evidence existed, so groundedness was not evaluated. | Deliver the graph answer, but trace it as not applicable rather than verified. |
| `unchecked_max_turns` | The safety cap produced a wrap-up answer and skipped the judge. | Deliver the fixed retry notice. |
| `check_error` | Judge execution or result parsing failed. | Deliver the fixed retry notice. |
| `rejected` | Unsupported content remained after the permitted revision. | Deliver the fixed retry notice. |

The fixed payload is:

```text
I couldn't verify this answer. Please retry.
```

It is a deterministic system message, not a model answer. It is delivered and persisted so conversation history matches what the user saw.

## Data flow

1. `groundedness.check()` returns its existing fields plus an explicit status. No evidence returns `not_applicable`; a judge exception or parse failure returns `check_error`; a valid judge result returns `verified` or `rejected`.
2. `groundedness_node` preserves the current single revision. A first `rejected` result requests a revision; the rechecked result becomes the final status. `check_error` and `not_applicable` do not trigger a rewrite.
3. The max-turn branch sets `unchecked_max_turns` before ending the graph.
4. With `SMARTDESK_VERIFIED_AGENT_DELIVERY` disabled, the existing two-stage behavior remains unchanged for a controlled comparison.
5. With the flag enabled, the router selects one canonical payload:
   - `verified` or `not_applicable`: `final_state["answer"]`;
   - all other statuses, including a missing/unknown status: the fixed retry notice.
6. The router emits the canonical payload as one SSE string frame, persists the identical Python string, and emits `[DONE]`. It makes no post-graph `llm_stream` call.

One SSE frame is intentionally used instead of artificial token-sized chunks. It preserves Unicode and whitespace byte-for-byte after JSON decoding and does not pretend to provide true model streaming. Existing tool/status frames provide progress before the final payload.

## Feature flag and compatibility

`SMARTDESK_VERIFIED_AGENT_DELIVERY` defaults to false. Only the LangGraph `route == "agent"` branch reads it. Direct, RAG, and legacy agent behavior remain unchanged.

Unknown or absent verification status fails closed to the retry notice when the flag is enabled. This prevents older or malformed graph state from being silently treated as verified.

## Observability and measurement

The enabled route writes an `agent_delivery` trace after persistence containing:

- `verification_status`;
- `delivery_kind` (`answer` or `retry_notice`);
- SHA-256 of the canonical payload, never the raw answer;
- `post_graph_generation_calls: 0`;
- payload character count.

Before/after comparison uses equivalent LangGraph agent requests and records:

- model generation calls per request;
- completion latency and time to first visible event;
- delivered/persisted mismatch count;
- verification-status distribution;
- output characters as the currently available cost proxy.

The Gemini client does not currently expose token or monetary usage. Exact tokens and cost must remain `unknown`; they must not be reported as zero. The experiment can still prove that one generation call was removed.

## Error handling

- No raw unchecked answer is sent or stored for `unchecked_max_turns`, `check_error`, `rejected`, or unknown status when the flag is enabled.
- A database failure remains an execution failure; the trace must not claim persistence before `commit()` succeeds.
- Client disconnect behavior is not redefined. Tests cover generator behavior and ensure the persistence path uses the selected canonical payload.

## Test strategy

Tests are written first and must fail for the expected missing behavior before production changes.

1. Groundedness result tests cover `verified`, `rejected`, `not_applicable`, and `check_error` without network calls.
2. Graph tests cover normal acceptance, revision then acceptance, rejection after one revision, no evidence, checker error, and max-turn status.
3. Router tests enable the feature flag and assert:
   - graph answer equals reconstructed SSE answer equals persisted answer for `verified` and `not_applicable`;
   - blocked statuses and unknown status produce and persist only the fixed retry notice;
   - no post-graph `llm_stream` call occurs;
   - direct, RAG, legacy, and flag-disabled behavior remain unchanged.
4. Trace tests assert the event is written only after successful persistence and contains the selected status, payload hash, and zero post-graph generation calls.

## Acceptance criteria

- Feature flag defaults off.
- With the flag on, the LangGraph agent performs zero model generations after graph completion.
- For every enabled-path success, reconstructed SSE text equals the stored `Conversation.answer` exactly.
- `verified` and `not_applicable` deliver the graph answer.
- `unchecked_max_turns`, `check_error`, `rejected`, missing status, and unknown status deliver and persist only the fixed retry notice.
- Revision output is checked again before it can receive `verified`.
- Existing direct, RAG, legacy-agent, checkpointing, and graph self-healing tests remain green.
- CASE-002 records the implementation commit, fresh verification output, known latency/call-count measurements, and unavailable cost fields as `unknown`.

## Rollout

Keep the flag off by default. Run the focused test suite, then a small replay set with the flag off and on. Adopt the enabled path only if payload mismatch falls to zero, the extra generation disappears, regressions remain absent, and the fixed retry rate is acceptable to the human owner.
