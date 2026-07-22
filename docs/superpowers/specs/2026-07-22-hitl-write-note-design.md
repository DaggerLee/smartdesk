# HITL Write Note Design

## Status

Approved and frozen for implementation on 2026-07-22. This specification
supersedes the implementation guidance in
`docs-local/HITL_Write_Tool_Design.md`. Any behavior change requires an
explicit design amendment before implementation continues.

## Goal

Build the minimum end-to-end human-in-the-loop write workflow on the existing
LangGraph checkpointer:

```text
write_note proposal
-> approve, edit, or reject
-> resume
-> execute at most once
-> read-back verification
-> receipt-derived final result
```

The first release writes Markdown files under the existing persistent `data`
directory. It does not add a Note database table, note CRUD API, note list, or
interactive approval UI.

## Governing invariants

- No filesystem side effect occurs before explicit structured approval.
- A write action succeeds at most once, including crash-and-resume windows.
- Reject never changes the filesystem.
- The LangGraph checkpoint is the single source of truth for pending-action
  state. There is no second pending-actions table or JSON store.
- The model never chooses an absolute path or a path relative to the service.
- A user can resolve only an action whose checkpoint records that user's ID.
- Proposal, action result, and final answer each have a distinct persistence
  boundary before their corresponding SSE frame.
- User-visible write claims come only from a committed action receipt, never
  from an unconstrained model completion.
- Full note content is not copied into ordinary traces or typed action
  evidence.
- Phase A preserves current production defaults: legacy backend and HITL write
  disabled.

## Scope and non-goals

In scope:

- A graph-only `write_note(title, content)` tool.
- Native LangGraph `interrupt()` and `Command(resume=...)`.
- Structured `approve | edit | reject` resolution.
- Per-user Markdown files under the existing Docker volume.
- Deterministic receipt-derived final answers.
- API-only approval driven by tests or curl.
- Minimal frontend handling for `[PAUSED]` and `[FAILED]`.
- A guarded, independently revertible LangGraph production cutover.

Not in scope:

- A Note model or note CRUD/list API.
- Approval buttons or an interactive confirmation card.
- Free-text approval interpretation.
- Batch or multi-write approval.
- Multi-worker exactly-once guarantees with `SqliteSaver`.
- A pending-action TTL or cleanup service.
- A full three-run real-model HITL evaluation in Phase A.
- Write support in the legacy agent loop.

## Feature flags and reachability

Two independent controls exist:

```text
SMARTDESK_AGENT_BACKEND=legacy|langgraph
SMARTDESK_HITL_WRITE_NOTE=false|true
```

Phase A defaults remain:

```text
SMARTDESK_AGENT_BACKEND=legacy
SMARTDESK_HITL_WRITE_NOTE=false
```

With these defaults, production behavior is unchanged: `write_note` is not
exposed, no write-intent gate changes legacy behavior, and legacy fail-closed
does not run.

Controlled development and smoke testing use:

```text
SMARTDESK_AGENT_BACKEND=langgraph
SMARTDESK_HITL_WRITE_NOTE=true
```

The final cutover uses the same pair, but changes the defaults only in a
separate commit after every cutover gate passes.

Any `SMARTDESK_AGENT_BACKEND` value other than `legacy` or `langgraph` is a
startup configuration error and fails fast instead of silently selecting a
backend. This behavior is deliberately implemented and tested only in the
independent Task 11 cutover commit; Phase A does not mix it into feature work.

Emergency rollback may set the backend to `legacy` while leaving
`SMARTDESK_HITL_WRITE_NOTE=true`. In that combination only, an explicit
persist request returns the fixed capability-unavailable notice before the
legacy agent is called. It creates no file or pending action and cannot let a
model claim success. The released notice is added to the append-only
`NON_CONTEXT_ANSWERS` set and is never removed or edited after release.

`write_note` is registered only in the graph tool registry and only when both
the HITL feature is enabled and the request has explicit persist intent. The
legacy tool registry remains unchanged.

## Shared write-intent classification

One focused shared module owns write-intent classification. Router, graph, and
legacy orchestration import it; none copies its rules.

The result vocabulary is:

```text
none | draft | persist
```

Classification favors precision over recall. Missing an unusual persistence
phrase is safer than interrupting an ordinary request.

`persist` requires unambiguous file-save intent, for example an explicit
request to save or write content as a note/file. Ambiguous phrases such as
"record this", "remember this", or "note that" do not independently qualify.
Explicit negation such as "draft only" or "do not save" produces `draft`.
Chinese rules are separate and do not use regex `\b` boundaries.

When HITL is disabled, the classifier cannot change request behavior. When
HITL is enabled:

- `persist` routes to `agent` deterministically;
- `draft` may route to agent for text generation but does not expose the write
  tool;
- `none` follows the existing router.

Before implementation changes production routing, existing Conversation and
eval query text is replayed through the classifier in read-only shadow mode.
The report contains all `persist` and `draft` candidates and changes no
database row. Because existing text has no human truth labels, the raw replay
must not claim a false-positive rate or precision. Such metrics may be
reported only after a human adjudicates all hits or a preregistered sample.

Gold cases include English and Chinese positive examples, explicit negations,
draft-only requests, and near negatives such as "record this", "remember
this", and "note that".

## Prompt single source of truth

The existing `SYSTEM_PROMPT` remains the shared base prompt for legacy and
graph agents. A separate `WRITE_NOTE_POLICY` is appended only by the graph
backend when the feature is enabled. The full base prompt is never copied.

The policy tells the model:

- call `write_note` only for explicit persist intent;
- provide only `title` and `content`;
- put `write_note` alone in its function-call round;
- treat the returned receipt as the only authority for write claims;
- never invent a path, success state, or verification result.

Runtime gates enforce all of these rules independently of prompt compliance.

## Graph topology

The graph-only write path is:

```text
llm_node
-> approval_gate
-> interrupt
-> Command(resume=resolution)
-> approval_gate
-> tool_node
-> receipt checkpoint
-> action_finalize_node
-> END
```

The terminal write branch explicitly bypasses `groundedness_node`:

```text
receipt committed
-> action_finalize_node
-> verification_status="verified"
-> verification_source="action_receipt"
-> delivery and persistence
```

Only a deterministically verified receipt can enter this branch. Ordinary
agent answers continue through groundedness and use
`verification_source="llm_groundedness"` when verified.

After tool execution, the graph appends a legal Gemini `functionResponse` to
checkpointed messages so the saved protocol state remains valid for future
extensions. Version 1 does not call Gemini again for an internal closing
sentence that would be discarded. It goes directly to
`action_finalize_node`. This removes one model call and shortens the resolve
lock duration.

For a mixed product request such as "summarize X and save it", version 1
delivers only the deterministic save receipt as the final chat answer. The
model-generated summary is stored in the file but is not also emitted as a
long chat response. A gold case freezes this accepted UX.

## Single-write round rule

Before executing any tool, the whole function-call round is inspected.

- A round with no `write_note` follows existing read-tool behavior.
- A round containing exactly one call and that call is `write_note` may
  proceed to proposal.
- A mixed read/write round executes nothing.
- A round with multiple writes executes nothing.

Every call in an invalid round receives a structured protocol error. The
failure uses a shared, bounded counter with the existing
`_MAX_TOOL_FAILURES` semantics. Once the limit is reached, the graph stops
asking the model to retry that invalid write round and terminates with a safe
failure result. It never loops indefinitely or executes a read call before
discovering the write violation.

Sequential work remains valid: a retrieve-only round may be followed by a
write-only round.

## Graph state and action lifecycle

Graph state gains trusted `user_id`, the graph `thread_id`, a
`pending_action`, and a per-run marker preventing a second write action.

The action stores:

- stable `action_id`;
- authenticated `user_id`;
- tool name;
- immutable `original_payload`;
- separate `approved_payload`;
- decision and optional reject reason;
- terminal receipt or safe error;
- lifecycle status.

Terminal status vocabulary is exactly:

```text
proposed | approved | rejected | succeeded | replayed | conflict | failed
```

`action_id` is generated by `llm_node` before the proposal is exposed and is
part of that node's synchronously durable checkpoint. Once exposed, resume
never regenerates it. `approve` copies the original payload into a distinct
approved payload. `edit` preserves the original and stores the complete edited
title and content separately. `reject` stores no approved payload.

One graph run processes at most one write action. A later write proposal in
the same run receives a bounded protocol failure and requires a new chat turn.

## Approval gate and resolution API

Before its `interrupt()` call, `approval_gate` may only read deterministic,
already-checkpointed state and construct the confirmation payload. It must not
create a directory, write a trace, execute a tool, mutate a business database,
generate an ID, or perform a time/random-dependent state change.

Resolution endpoint:

```http
POST /api/chat/actions/{thread_id}/resolve
Authorization: Bearer <JWT>
```

Strict request forms are:

```json
{"action_id":"...","decision":"approve"}
```

```json
{"action_id":"...","decision":"edit","title":"...","content":"..."}
```

```json
{"action_id":"...","decision":"reject","reason":"optional"}
```

Extra fields are rejected. `approve` accepts no title, content, or reason.
`edit` requires complete title and content. `reject` accepts no title or
content and may include a reason. A client-supplied user ID is an extra field
and is rejected.

The server loads the checkpoint and verifies
`pending_action.user_id == current_user.id`. Knowing a thread or action ID is
not authorization. Unknown and unauthorized actions both return 404 to avoid
existence disclosure. State, resolution, and file conflicts return 409.

A per-thread resolve lock covers the complete critical section: checkpoint
load, ownership check, latest-checkpoint reread, `Command(resume=...)`, action
result commit, graph finalization, and Conversation commit. The lock coordinates
the current single-process deployment but is not the action source of truth.

Resolution identity includes the decision and the full approved payload, or
the normalized reject reason. An exact repeat returns the stored result. Any
different payload, reason, or decision conflicts. A committed `failed` result
is terminal; retry requires a new chat action ID. A crash before a terminal
checkpoint may safely replay from the last committed task.

## Input validation

Title:

- Unicode string of 1 through 120 code points;
- equals its own trimmed value and is not whitespace-only;
- contains no `/`, `\`, NUL, Unicode control character;
- is not exactly `.` or `..`.

Content:

- Unicode string of 1 through 50,000 code points;
- is not whitespace-only;
- contains no NUL;
- is not truncated and otherwise preserves supplied whitespace.

Reject reason:

- absent/null or a Unicode string of 1 through 500 code points;
- equals its own trimmed value;
- contains no NUL.

Validation failure rejects the request. Dangerous title separators are never
silently slugified into acceptance.

## Filesystem layout and path safety

Docker already mounts:

```text
smartdesk_data:/app/data
```

The internal per-user note directory is:

```text
/app/data/users/<authenticated-user-id>/notes/
```

The server-generated filename is:

```text
<safe-slug>-<full-action-id>.md
```

Only `notes/<filename>` is user-visible. Absolute paths and the internal user
directory are never returned.

The service verifies every root/user/notes path component and rejects symbolic
links. Linux execution uses directory file descriptors and no-follow flags.
The model never supplies a path. The final target must be a regular file or
absent and is never overwritten.

## Atomic publication and execute-once semantics

Approved title and content are serialized deterministically as UTF-8 Markdown:

```text
# <approved title>\n
\n
<approved content>\n
```

The implementation creates a temporary regular file in the final directory,
writes all canonical bytes, flushes and fsyncs it, and publishes it using an
atomic no-clobber operation on the same filesystem. The directory is fsynced.
The final file is reopened and read completely. Success requires exact byte
equality with the approved canonical content.

If the final target already exists:

- exact canonical bytes produce `replayed`, with no write or overwrite;
- any difference produces `conflict`, with no overwrite.

This covers a crash after atomic publication but before the tool node's
checkpoint: resume reads the same completed file and records `replayed` rather
than writing again. Reject never creates the user or notes directory.

## Action receipt and deterministic final answer

A receipt contains at least:

- `action_id`;
- tool name;
- result (`succeeded`, `replayed`, `rejected`, `conflict`, or `failed`);
- user-visible relative path when applicable;
- SHA-256 content hash when applicable;
- byte count when applicable;
- read-back verification flag;
- safe error code when applicable.

Full note content and reject reason do not enter ordinary traces or typed
action evidence. A redacted typed `action_receipt` evidence object contains
only receipt metadata.

`action_finalize_node` validates the committed receipt and constructs the
canonical answer from fixed templates. It copies only facts present in the
receipt. Model-generated write claims are never selected for delivery.

Ordinary user-facing templates do not show SHA-256, byte count, or error code.
Those fields remain in the receipt, checkpoint, and safe trace. User text says
only whether the action succeeded, was safely replayed, was rejected,
conflicted, or failed, plus the relative path for success/replay.

The graph records:

```text
verification_status="verified"
verification_source="action_receipt"
```

for a deterministically verified action receipt. Ordinary grounded answers use
`verification_source="llm_groundedness"`. The meaning is added to
`SmartDesk_Decisions.md` when the feature merges.

An `action_receipt` terminal bypasses the ordinary verified-delivery feature
flag and its two-stage answer-selection branch. When
`verification_source == "action_receipt"`, delivery always selects the
canonical `GraphState.answer` constructed by `action_finalize_node`; it never
selects `llm_stream`, never calls Gemini after graph completion, and therefore
has exactly zero post-graph model calls. The flag-on and flag-off paths are
required to produce the same receipt answer and the same zero-call count.

The identical canonical answer is committed to `Conversation.answer` before
any final answer frame is emitted. The emitted SSE text and persisted
`Conversation.answer` must be byte-for-byte identical. Tests cover both
verified-delivery flag states, prove the identity invariant, and prove no
additional Gemini call occurs.

## SSE persistence boundaries

The stream has three independent publication boundaries.

### Confirmation

The confirmation frame is emitted only after a persisted checkpoint contains
the exact proposal and active interrupt. The adapter rereads checkpoint state
before emitting:

```json
{"confirmation_required":{...}}
```

The request then ends with `[PAUSED]`. Proposal/checkpoint failure emits a
typed error with `stage="proposal"`, then `[FAILED]`, and creates no
Conversation.

### Action result

`tool_node` must not emit an early success event from inside the uncommitted
node. The adapter waits for the terminal action checkpoint, rereads it, and
only then emits `action_result`. A checkpoint failure emits an error with
`stage="action_result"`, then `[FAILED]`. A previously published file remains
recoverable by the execute-once logic.

### Final answer

The canonical receipt answer is emitted only after the identical
`Conversation.answer` commits. A Conversation failure emits an error with
`stage="conversation"`, then `[FAILED]`, and no final answer frame. A failure
after a valid action result may therefore leave the client knowing that the
file action succeeded while the conversational finalization failed.

`[DONE]` is emitted only after the final answer. No stage is summarized by a
generic "commit-before-emit" claim; each boundary has its own test.

## Conversation thread mapping

`Conversation` gains a nullable `thread_id`. Startup migration is repeatable:

```sql
ALTER TABLE conversations ADD COLUMN thread_id TEXT NULL;
```

```sql
CREATE UNIQUE INDEX IF NOT EXISTS ix_conversations_thread_id_unique
ON conversations(thread_id)
WHERE thread_id IS NOT NULL;
```

Existing rows remain null. Paused requests create no Conversation. Completed
graph requests insert by thread ID. A duplicate completion reuses a row only
when KB, question, and answer match exactly; otherwise it conflicts. This
mapping is not a second action source of truth.

## API-only client boundary

Version 1 approval is driven by the resolve API, tests, or curl. Approval
buttons are a separate product task.

The current frontend must nevertheless recognize `[PAUSED]` and `[FAILED]`,
end loading for both, and avoid rendering either token as answer text.
`[PAUSED]` displays at least a status that the operation is waiting for
confirmation. Tests prove both terminal tokens settle the request correctly.
The design does not claim the existing client necessarily hangs forever when
a stream ends naturally; the new behavior is explicit and test-backed.

## Trace and privacy

Safe trace events cover proposal, resolution, execution, replay, rejection,
conflict, and failure. They may contain thread ID, action ID, result, edited
flag, relative path, hash, byte count, read-back flag, and safe error code.
They do not contain note title/content, reject reason, absolute path, tokens,
or raw blocked answers.

Trace and typed-evidence tests assert an explicit field whitelist, not only
value redaction. Neither surface may contain `title`, `content`, reject
`reason`, `original_payload`, or `approved_payload`. Only the approved receipt
metadata fields listed above may cross this boundary; adding a field requires
an intentional schema and test change.

The legacy capability-unavailable notice is a frozen policy constant and is
added to the append-only non-context set.

## Gold and deterministic test coverage

The HITL gold protocol includes:

- English and Chinese explicit persist requests;
- English and Chinese draft-only/negated requests;
- near negatives such as "record this", "remember this", and "note that";
- normal write proposal;
- ordinary knowledge question with no write exposure;
- approve, edit, and reject;
- mixed read/write and multiple-write protocol failures;
- summarize-and-save, with receipt-only final chat UX;
- succeeded, replayed, rejected, conflict, and failed receipt outcomes.

Deterministic tests cover action identity, ownership, strict schemas, original
versus edited payload, no pre-interrupt effects, whole-round validation,
bounded protocol failures, atomic publication, symlink escape, crash replay,
resolution conflicts, each SSE persistence boundary, Conversation idempotency,
legacy fail-closed, and frontend paused/failed completion.

## Production cutover gate

Phase A implementation and tests leave the default backend as legacy and HITL
disabled. Before changing defaults, full HTTP chat-route tests cover:

- direct: delivered SSE text, `[DONE]`, Conversation persistence, and history;
- RAG: delivered text, sources frame, exact Conversation.answer identity, and
  history;
- agent: existing real graph/SSE regressions plus HITL pause/resume.

Node tests alone do not satisfy this gate. Any failure prevents cutover.

After all deterministic and HITL tests pass, the final default switch is a
separate commit that changes config, `.env.example`, and Docker defaults to
LangGraph plus HITL enabled. It contains no feature implementation and can be
reverted independently.

## Real Gemini verification gate

Feature completion cannot be claimed using only fake-LLM tests. The order is:

1. deterministic tests;
2. full existing test suite;
3. committed HITL gold cases;
4. Docker volume/path verification;
5. notify the user of the exact smoke query, decision, flags, expected Gemini
   request count, and paid-quota scope;
6. after that notice, run one minimum real Gemini smoke covering router,
   `functionCall`, interrupt, resume, receipt, and final delivery.

Because version 1 removes the post-receipt Gemini continuation, the expected
minimum is the router request plus the tool-proposal request. Actual calls are
recorded; token and monetary cost remain `unknown` when not exposed. A full
three-run HITL evaluation may follow later.

No real Gemini call is authorized by this specification itself.

## Delivery sequence

Implementation delivery is separated into:

1. this design-document commit;
2. Phase A TDD feature commits with defaults unchanged;
3. deterministic and gold verification;
4. user-notified real smoke gate;
5. an independent default-cutover commit only if every gate passes.

The branch is not merged or pushed during this task unless the user later asks.
