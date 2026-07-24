# SmartDesk Portfolio README Design

**Date:** 2026-07-24
**Status:** Approved by the human on 2026-07-24
**Scope:** Public presentation only; no product behavior changes

## Goal

Make the public repository accurate first, then make the verified engineering
work understandable to a recruiting manager or interviewer within 30 seconds.
This is a human-authorized portfolio milestone, not a reopening of the
SmartDesk product roadmap.

## Delivery structure

The work is split into two independently reviewable implementation commits.

### Commit A — Accuracy correction

Commit A must stand on its own and may be merged without Commit B.

- Remove stale progress claims such as `W2 in progress` and `17 unit tests`.
- Replace them only with verified facts owned by
  `docs/PROJECT_EVIDENCE.md`.
- Repair stale document links, architecture descriptions, and run
  instructions.
- Do not add portfolio storytelling, new diagrams, or promotional claims.

### Commit B — Portfolio presentation

- Lead with the problem, the verified outcome, and the engineering decisions.
- Add a concise system architecture diagram and a separate HITL write path.
- Add evidence-backed highlights, limitations, and an evidence index.
- Update the GitHub About description and repository Topics after the README
  content is accepted.
- Preserve accurate quick-start instructions and keep detailed implementation
  material below the portfolio summary.

## Evidence contract

`docs/PROJECT_EVIDENCE.md` is the semantic owner for public achievement claims.
README numbers must be copied from it, not recomputed or inferred.

Allowed current claims include:

- 258 backend tests and 5 frontend terminal-handling tests for the HITL
  production cutover, plus a 73-module production build.
- 11 frontend tests and a 75-module production build for the final Markdown
  rendering correction.
- A 35-item eval set and the recorded baseline values in EV-001.
- One verified local and one verified Docker real-model HITL success, with
  exact token and monetary cost remaining unknown.
- API-only browser acceptance for distinct paused and failed terminal states
  using a deterministic zero-Gemini mock.

Claims must retain their limitations. Single runs are not statistical
conclusions, mock browser acceptance is not a live-model browser run, and
unknown cost must not be reported as zero.

## XSS wording

Use: **the observed Markdown XSS was fixed and is guarded by regression
tests**.

Evidence:

- `56d4fc3` — sanitization implementation
- `9d7e889` — unique trust-boundary guard
- `29bac52` — rendering-contract coverage
- PR #3 / merge `1a026c4`

Do not describe this focused rendering-boundary correction as a complete
security audit, CSP rollout, or backend sanitization framework.

## Architecture truth

The overview diagram may simplify presentation but must preserve real
boundaries:

- router paths are `direct`, `rag`, and `agent`;
- LangGraph owns workflow orchestration;
- MCP owns tool exposure;
- SQLite checkpoints provide the current single-process/demo durability model;
- verified delivery commits the canonical answer before SSE emission.

The HITL write path must show:

```text
llm_node
  -> approval_gate
  -> interrupt
  -> Command(resume)
  -> write_action_node
  -> action_finalize_node
  -> END
```

Rejection routes from `approval_gate` directly to `action_finalize_node`.
Successful and rejected action receipts bypass the ordinary groundedness path;
the committed action receipt is the verification source for write claims.

## Public repository metadata

After README approval:

- update the GitHub About description to match the verified current project;
- add a small set of accurate Topics, such as `rag`, `langgraph`, `fastapi`,
  `vue`, `human-in-the-loop`, `llm-evaluation`, and `mcp`;
- leave Contributors untouched.

GitHub derives Contributors from commit authorship. AI tools must not be
presented as human contributors.

## Non-goals

- No product code, configuration, dependency, test, or eval changes.
- No approval UI, mobile-layout fix, demo deployment, or new screenshot
  production unless separately authorized.
- No rewriting or staging of the 50 user-owned eval artifacts.
- No claim that SmartDesk is production-scaled or fully security-audited.

## Acceptance

- Commit A contains accuracy corrections only and remains independently
  mergeable.
- Commit B contains portfolio presentation only.
- Every metric and outcome traces to `docs/PROJECT_EVIDENCE.md`.
- The architecture and HITL diagrams match the implemented topology.
- `git diff --check` passes, links resolve within the repository, and the
  rendered README is inspected before merge.
