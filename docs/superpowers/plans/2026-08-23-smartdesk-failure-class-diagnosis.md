# SmartDesk Failure-Class Diagnosis Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Diagnose the residual eval failure classes from the archived `ef70ae4` holdout runs without changing code, labels, prompts, datasets, or result artifacts.

**Architecture:** This is a read-only evidence pass. Each task pins one failure layer, reads the canonical gold/result artifacts and source excerpts, and emits an evidence-backed classification plus any proposed next experiment. Implementation fixes and label changes are explicitly out of scope.

**Tech Stack:** Python JSONL inspection, SmartDesk eval artifacts, FastAPI backend eval harness, Chroma retrieval, Gemini router/RAG/agent paths.

**Spec:** Accepted session contract in `local://successor-holdout-goal.md`, reviewer gate `HardeningBlueprintReviewer`, and hardened governance in `AGENTS.md` plus `backend/eval/GOLD_SET_CHANGELOG.md`.

## Global Constraints

- Canonical checkout only: `/home/dagger306/projects/smartdesk`. Do not read or edit `/mnt/c/Users/Dagger/smartdesk`.
- Read-only diagnosis: do not edit source, prompts, tests, datasets, result JSONL, history, checkpoint DB, or changelog during diagnosis.
- Do not run paid evals during diagnosis. Use existing archived artifacts only.
- Gold/holdout label corrections may only be proposed with source evidence; applying any label or keyword change requires explicit user approval.
- Keep dataset identity explicit. `x*` rows are from successor3; `h*` rows are from the burned 2026_08 holdout. Do not pool their metrics or treat the burned set as fresh evidence.
- Result artifacts are user-owned evidence. Do not normalize, rewrite, deduplicate, rescore, or regenerate them.
- Avoid sample-specific fixes. Any later fix must target a confirmed failure class, not a row ID or holdout query.

---

## Evidence Inventory

**Reviewed code/data identity:**
- Source HEAD under evaluation: `ef70ae4`.
- Archive commit containing result artifacts: `663db2d`.
- Governance hardening commit: `0a3755d`.
- Burned regression dataset: `backend/eval/holdout_set_2026_08.jsonl`.
- Burned regression result: `backend/eval/results/holdout_rerank_ef70ae4_20260823.jsonl`.
- Successor3 dataset: `backend/eval/holdout_set_2026_08_successor3.jsonl`.
- Successor3 result: `backend/eval/results/holdout_successor3_ef70ae4_20260823.jsonl`.
- Summary artifact: `backend/eval/results/holdout_ef70ae4_20260823_summary.json`.

**Rows to diagnose:**
- `x007` successor3: expected `rag`, actual `agent`; retrieval/relevance/contains/grounded pass. Route-only miss.
- `x014` successor3: expected/actual `rag`; retrieval/relevance/grounded pass; contains `2`, min_hits `4`. Answer coverage miss or keyword-contract issue.
- `h012` burned 2026_08: expected/actual `rag`; retrieval/relevance pass; contains `3`, min_hits `4`; grounded false. Generation overreach, evidence mismatch, or keyword-contract issue.
- `h013` burned 2026_08: expected `rag`, actual `agent`; contains pass; unanswerable row where agent answers beyond the corpus. Route/source-boundary miss.
- `h014` burned 2026_08: expected `rag`, actual `agent`; contains pass; unanswerable row where agent performs extended search-style reasoning. Route/source-boundary miss.

---

### Task 1: Pin Artifact Evidence

**Files:**
- Read: `backend/eval/holdout_set_2026_08_successor3.jsonl`
- Read: `backend/eval/results/holdout_successor3_ef70ae4_20260823.jsonl`
- Read: `backend/eval/holdout_set_2026_08.jsonl`
- Read: `backend/eval/results/holdout_rerank_ef70ae4_20260823.jsonl`
- Read: `backend/eval/results/holdout_ef70ae4_20260823_summary.json`

**Interfaces:**
- Consumes: archived JSONL result rows and gold rows.
- Produces: a per-row evidence table used by Tasks 2-5.

- [ ] **Step 1: Extract the five row contracts and results**

Run from `/home/dagger306/projects/smartdesk/backend`:

```bash
python3 - <<'PY'
import json
from pathlib import Path
pairs = [
    (
        'successor3',
        'eval/holdout_set_2026_08_successor3.jsonl',
        'eval/results/holdout_successor3_ef70ae4_20260823.jsonl',
        ['x007', 'x014'],
    ),
    (
        'burned-2026-08',
        'eval/holdout_set_2026_08.jsonl',
        'eval/results/holdout_rerank_ef70ae4_20260823.jsonl',
        ['h012', 'h013', 'h014'],
    ),
]
for label, gold_path, result_path, ids in pairs:
    gold = {json.loads(line)['id']: json.loads(line) for line in Path(gold_path).read_text().splitlines() if line.strip()}
    results = {json.loads(line)['id']: json.loads(line) for line in Path(result_path).read_text().splitlines() if line.strip()}
    print(f'## {label}')
    for row_id in ids:
        g = gold[row_id]
        r = results[row_id]
        print(row_id, {
            'query': g['query'],
            'expected_route': g['expected_route'],
            'actual_route': r.get('actual_route'),
            'route_correct': r.get('route_correct'),
            'min_hits': g.get('min_hits'),
            'expected_answer_contains': g.get('expected_answer_contains'),
            'contains_hits': r.get('contains_hits'),
            'contains_pass': r.get('contains_pass'),
            'retrieval_hit': r.get('retrieval_hit'),
            'relevance_ok': r.get('relevance_ok'),
            'grounded': r.get('grounded'),
            'answer_scope': r.get('answer_scope'),
            'verification_status': r.get('verification_status'),
            'llm_call_count': r.get('llm_call_count'),
            'llm_retry_count': r.get('llm_retry_count'),
            'latency_s': r.get('latency_s'),
            'answer': (r.get('answer') or '')[:1000],
        })
PY
```

Expected: prints exactly five rows. Do not write files.

- [ ] **Step 2: Record evidence identity in the diagnosis notes**

Write the evidence table into the session response or a temporary local note only. Do not commit generated diagnosis notes unless the user asks.

---

### Task 2: Diagnose `x007` Route-Only Miss

**Files:**
- Read: `backend/eval/holdout_set_2026_08_successor3.jsonl` row `x007`
- Read: `backend/eval/results/holdout_successor3_ef70ae4_20260823.jsonl` row `x007`
- Read: `backend/agent/router.py`
- Optional read: `backend/tests/test_router.py`
- Optional read: `docs-local/notes/Agentic_AI_Distilled_Notes.html:228-234`

**Interfaces:**
- Consumes: Task 1 evidence table.
- Produces: route-layer classification for `x007`.

- [ ] **Step 1: Confirm failure layer**

Expected row facts:

```text
query: Code Execution Tool 为什么比堆很多数学专用工具更合适？
expected_route: rag
actual_route: agent
retrieval_hit: True
relevance_ok: True
contains_pass: True
grounded: True
answer_scope: agent_internal
verification_status: verified
```

Decision rule: if route failed but answer quality passed, classify as route-only unless the gold route itself appears wrong.

- [ ] **Step 2: Check whether the expected route is contractually plausible**

Read router rules. `rag` covers a single factual question expected to exist in the KB with one retrieval sufficient. `agent` covers comparison/synthesis/planning/multiple retrieval/current external lookup.

For `x007`, the question asks one source-bounded factual rationale about Code Execution Tool. It does not ask for comparison across independent sources; “比堆很多数学专用工具” is a same-topic technical rationale, not necessarily multi-source synthesis.

- [ ] **Step 3: Form hypothesis**

Candidate hypothesis:

```text
The router over-weights comparative phrasing such as “为什么 X 比 Y 更合适” and promotes a single-source factual rationale to agent. This is factual-query complexity over-routing, not retrieval or generation failure.
```

- [ ] **Step 4: Define later bounded experiment, without implementing it**

Later experiment candidate:

```text
Add or adjust a general router invariant for source-bounded same-topic rationale/comparison staying rag when one retrieval can answer it. Do not use x007 wording or Code Execution Tool/math-tools semantics as a prompt example.
```

Do not edit router prompt in this diagnosis task.

---

### Task 3: Diagnose `x014` Answer-Coverage Miss

**Files:**
- Read: `backend/eval/holdout_set_2026_08_successor3.jsonl` row `x014`
- Read: `backend/eval/results/holdout_successor3_ef70ae4_20260823.jsonl` row `x014`
- Read: `docs-local/notes/TinaHuang_AI_Distilled_Notes.html:170-177`

**Interfaces:**
- Consumes: Task 1 evidence table.
- Produces: answer-layer classification for `x014`.

- [ ] **Step 1: Confirm failure layer**

Expected row facts:

```text
query: Tina 的 traceability 文档为什么要同时配可运行 Python script？
expected_route: rag
actual_route: rag
retrieval_hit: True
relevance_ok: True
grounded: True
contains_hits: 2
contains_pass: False
min_hits: 4
```

- [ ] **Step 2: Compare answer with keyword contract**

Expected groups:

```text
traceability
用了什么数据|what data
怎么做|how
有效性威胁|threats to validity
可独立运行|Python script|Jupyter notebook
```

Observed answer summarized:

```text
It explains that runnable Python scripts solve the problem of analysis results being trapped in Jupyter notebooks and improve reproducibility.
```

- [ ] **Step 3: Check source passage**

Source lines 170-177 contain two adjacent facts:

```text
traceability README has three elements: data used, how analysis was performed, threats to validity.
Each analysis/visualization should also have a complete independently runnable Python script to avoid results trapped in Jupyter notebooks.
```

- [ ] **Step 4: Form hypotheses**

Candidate hypotheses:

```text
Primary: answer synthesis collapsed the traceability-document details and only answered the Python-script half, so it missed requested subpoints even though retrieval and groundedness passed.
Secondary: the row may be over-demanding if the question only asks why the Python script is paired, not what the traceability document's three elements are. If so, propose a keyword/label correction with source evidence; do not apply without user approval.
```

- [ ] **Step 5: Define later bounded experiment, without implementing it**

Later experiment candidate:

```text
If user rejects label correction, inspect eval RAG prompt for supported-subpoint coverage on “why A with B” questions. A fix must be phrased as a general answer-completeness invariant, not a Tina/traceability example.
```

---

### Task 4: Diagnose `h012` Sampling Coverage/Groundedness Miss

**Files:**
- Read: `backend/eval/holdout_set_2026_08.jsonl` row `h012`
- Read: `backend/eval/results/holdout_rerank_ef70ae4_20260823.jsonl` row `h012`
- Read: `docs-local/notes/MCP_Distilled_Notes.html:194-195`

**Interfaces:**
- Consumes: Task 1 evidence table.
- Produces: classification for burned-set residual `h012`.

- [ ] **Step 1: Confirm failure layer**

Expected row facts:

```text
query: MCP Sampling 的价值和控制权边界是什么？
expected_route: rag
actual_route: rag
retrieval_hit: True
relevance_ok: True
contains_hits: 3
contains_pass: False
grounded: False
min_hits: 4
```

- [ ] **Step 2: Compare expected groups with answer**

Expected groups:

```text
server 向 client|借你的 LLM|借脑
数据不出门|省 token
server 保持无脑|不用配 key|不绑模型
client 有权拒绝|限量|控成本
模型偏好|temperature
```

Observed answer includes the sampling mechanism, data not leaving, safety/token value, and server/client control framing, but failed one expected group and groundedness.

- [ ] **Step 3: Check source passage**

Source lines 194-195 include the exact contract surface:

```text
server asks client to borrow its LLM; value is data stays private and saves tokens; server stays dumb/no key/no model binding; request can include model preference/temperature, but client may ignore/refuse/limit/control cost.
```

- [ ] **Step 4: Form hypotheses**

Candidate hypotheses:

```text
Primary: answer generation over-expanded or paraphrased the control-boundary details enough for contains to miss and groundedness to reject.
Secondary: keyword groups may be too strict around “模型偏好/temperature” if the answer gives the boundary but omits that specific negotiable-parameter detail. This would be only a proposed label/keyword correction pending user approval.
```

- [ ] **Step 5: Define later bounded experiment, without implementing it**

Later experiment candidate:

```text
Inspect whether eval RAG prompt encourages over-structured causal wording that can introduce unsupported framing. If implementing later, target concise source-faithful coverage for dense protocol-boundary rows; do not tune to h012 text.
```

---

### Task 5: Diagnose `h013` and `h014` Corpus-Negative Over-Routing

**Files:**
- Read: `backend/eval/holdout_set_2026_08.jsonl` rows `h013`, `h014`
- Read: `backend/eval/results/holdout_rerank_ef70ae4_20260823.jsonl` rows `h013`, `h014`
- Read: `backend/agent/router.py`
- Optional read: `backend/tests/test_router.py`

**Interfaces:**
- Consumes: Task 1 evidence table.
- Produces: route/source-boundary classification for corpus-negative rows.

- [ ] **Step 1: Confirm failure layers**

Expected row facts:

```text
h013 query: PostgreSQL 的 transaction isolation levels 在这四份笔记里怎么调优？
expected_route: rag
actual_route: agent
contains_pass: True
answer_scope: agent_internal
verification_status: unchecked_max_turns

h014 query: CSS Grid 的 subgrid 布局在这四份笔记里怎么用？
expected_route: rag
actual_route: agent
contains_pass: True
answer_scope: agent_internal
verification_status: unchecked_max_turns
```

- [ ] **Step 2: Preserve per-layer interpretation**

Do not reduce these rows to contains pass. Contains passes because the answer includes absence language and the topic terms, but route failed and the agent path can add out-of-corpus guidance.

- [ ] **Step 3: Form hypothesis**

Candidate hypothesis:

```text
Corpus-bounded absence questions with domain-specific technical terms are over-routed to agent, especially when phrased “在这四份笔记里怎么...”. The intended behavior is rag: retrieve/check corpus and answer absence. The current route leaks into agent_internal, where unchecked_max_turns can produce extra out-of-corpus guidance.
```

- [ ] **Step 4: Define later bounded experiment, without implementing it**

Later experiment candidate:

```text
Strengthen the general router invariant for corpus-bounded absence checks. The prompt/example must not use PostgreSQL transaction isolation, CSS subgrid, or any burned/unscored holdout semantics. Consider synthetic non-project examples only after reviewer approval.
```

---

### Task 6: Produce Diagnosis Report and Next Experiment Gate

**Files:**
- Read-only inputs from Tasks 1-5.
- No source or artifact modifications.

**Interfaces:**
- Consumes: per-row classifications.
- Produces: a concise report for the user plus a proposed next experimental plan.

- [ ] **Step 1: Group rows by confirmed layer**

Expected grouping if Tasks 2-5 confirm hypotheses:

```text
Route over-routing:
- x007: source-bounded factual rationale over-routed to agent.
- h013/h014: corpus-negative absence checks over-routed to agent.

Answer coverage / possible keyword-contract review:
- x014: traceability/Python-script answer covered reproducibility but omitted traceability-document subpoints.
- h012: sampling answer missed or over-paraphrased one dense control-boundary group and groundedness rejected.
```

- [ ] **Step 2: State what is blocked on user approval**

If a label or keyword issue appears plausible, output exactly:

```text
Proposed label/keyword correction only; not applied. Requires explicit user approval before editing any gold/holdout file or rescoring.
```

- [ ] **Step 3: Propose next code-change experiment, not implementation**

The likely next experiment should be one of:

```text
A. Router-only general invariant for source-bounded factual rationale and corpus-bounded absence checks.
B. Eval RAG answer-completeness invariant for dense source-backed subpoint coverage.
C. No code change; propose specific gold/keyword correction(s) for user approval.
```

Choose only after evidence supports the layer. Do not bundle A and B in one implementation, because that would blur causality.

- [ ] **Step 4: Request review before any implementation**

Before code changes, request reviewer/advisor gate with the diagnosis report, exact proposed invariant, and proof that no prompt examples derive from burned or unscored holdout rows.

---

## Self-Review Checklist

- Spec coverage: all five named residual rows are covered exactly once, with dataset identity pinned.
- Gap marker scan: no unresolved markers remain.
- Type consistency: all file paths and artifact names match existing repo files.
- Safety: no step edits source, labels, datasets, result artifacts, history, or checkpoint files.
- Governance: label/keyword changes are proposal-only pending explicit user approval.
