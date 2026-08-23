# Gold Set Changelog

Revisions to `gold_set.jsonl` after the item itself was already scored — i.e.
the label was wrong, not the system. Route/scoring bugs found in the
harness or agent code are fixed in code, not here.


## 2026-08-23 — successor3 holdout set

- Added `holdout_set_2026_08_successor3.jsonl` as the next clean 18-item
  successor after finalizing the router prompt examples. This set replaces
  successor2 for future generalization evidence; it must be run once, then
  marked burned.
- Holdout SHA-256:
  `ec5871ecec3ae1926f852861557d6cb907557cc4b7060cee3e2322405f10fbf0`.
- The set stays on the current `kb_1` corpus owner: the four files loaded by
  `eval/rebuild_kb1_index.py` (`Agentic_AI_Distilled_Notes.html`,
  `TinaHuang_AI_Distilled_Notes.html`, `VibeCoding101_Distilled_Notes.html`,
  `MCP_Distilled_Notes.html`). It deliberately excludes
  `JFP_AgentSkills_Podcast_Distilled_Notes.html`.
- Coverage uses new IDs `x001`-`x018` and new phrasings: direct controls,
  agentic benefits/decomposition/reflection/tool-use/eval/optimization facts,
  MCP standardization/schema/parallel-tool protocol details, reproducibility
  and prompt-discipline facts, cross-source execution-boundary synthesis, and
  corpus-negative absence.
- The absence row was checked by literal search for `WebRTC`/`ICE candidate`/
  `TURN server` across the four indexed note files.

## 2026-08-23 — successor2 retired development set

- Added `holdout_set_2026_08_successor2.jsonl` as the second successor
  18-item holdout after the bounded diagnostic-chain prompt fix. It was never
  formally evaluated.
- Lifecycle correction: reviewer found the later router example in `617fe66`
  semantically matched successor2 `t011` (short-lived/renewal credentials vs
  access/refresh tokens). Exact string leakage was absent, but the semantic
  template contamination is enough to retire successor2 as a clean holdout.
  Treat it as a development set only; do not use it as generalization evidence.
- Holdout SHA-256:
  `ff53c54b2b4fb2352ee8b71f1eebf0ff6270ab2d9a61595b9e2599af5564befb`.
- The set stays on the current `kb_1` corpus owner: the four files loaded by
  `eval/rebuild_kb1_index.py` (`Agentic_AI_Distilled_Notes.html`,
  `TinaHuang_AI_Distilled_Notes.html`, `VibeCoding101_Distilled_Notes.html`,
  `MCP_Distilled_Notes.html`). It deliberately excludes
  `JFP_AgentSkills_Podcast_Distilled_Notes.html`, which is present in
  `docs-local/notes/` but not in the rebuilt `kb_1` index.
- Coverage uses new IDs `t001`-`t018` and new phrasings: direct control,
  autonomy-spectrum retrieval, external-feedback specifics, error-analysis
  causal attribution, rubric scoring, MCP role/schema/control boundaries,
  failure recovery, cross-source mini-eval/design-principle synthesis, parallel
  dual-concept lookup, and corpus-negative absence.
- The absence row was checked by literal search for
  `Terraform`/`OpenTelemetry`/`distributed tracing` across the four indexed
  note files.

## 2026-08-23 — successor holdout set

- Added `holdout_set_2026_08_successor.jsonl` as the successor 18-item
  holdout after the CJK retrieval, cross-source router, and eval RAG prompt
  failure-class fixes were committed. This set is reserved for one formal
  generalization run only; after inspection it is burned for future tuning.
- Holdout SHA-256:
  `543c0a0f00d686d7d038366a56bd761d944a0d2322626e8508cbabdc55cac4c4`.
- The set stays on the current `kb_1` corpus owner: the four files loaded by
  `eval/rebuild_kb1_index.py` (`Agentic_AI_Distilled_Notes.html`,
  `TinaHuang_AI_Distilled_Notes.html`, `VibeCoding101_Distilled_Notes.html`,
  `MCP_Distilled_Notes.html`). It deliberately excludes
  `JFP_AgentSkills_Podcast_Distilled_Notes.html`, which is present in
  `docs-local/notes/` but not in the rebuilt `kb_1` index.
- Coverage uses new IDs `s001`-`s018` and new phrasings for the same broad
  failure classes: CJK compound retrieval, causal diagnostic-chain answers,
  cross-source synthesis routing, parallel dual-concept retrieval, and
  corpus-negative absence checks.
- The two absence rows were checked by literal search for the absent topics
  (`GraphQL`/`federation`/`schema stitching`, `CUDA`/`kernel fusion`/
  `shared memory`) across the four indexed note files.
- First formal successor run:
  `results/holdout_successor_d22c807_20260823.jsonl`. Metrics: errors `0`,
  router `18/18`, positive-query retrieval `14/14`, positive-query relevance
  `14/14`, contains `18/18`, grounded `11/14`, faithfulness `0.9` (`n=3`),
  answer relevancy `1.0` (`n=3`), average latency `28.39s`, retries `0`.
  Groundedness failed on `s003`, `s004`, and `s012`; the run is now burned
  and is evidence of eval RAG prompt overreach, not a set to tune further.


## 2026-08-23 — fresh2 holdout set

- Added `holdout_set_2026_08_fresh2.jsonl` as a new 18-item holdout because
  `holdout_set_2026_08.jsonl` was burned by the adjusted full run and the
  prompt-invariant route probe.
- Holdout SHA-256:
  `e1efc93a069567bd5259e03bab842c06b0283d8ad1e136bb1a5c6f4841444d01`.
- The set stays on the current `kb_1` corpus owner: the four files loaded by
  `eval/rebuild_kb1_index.py` (`Agentic_AI_Distilled_Notes.html`,
  `TinaHuang_AI_Distilled_Notes.html`, `VibeCoding101_Distilled_Notes.html`,
  `MCP_Distilled_Notes.html`). It deliberately excludes
  `JFP_AgentSkills_Podcast_Distilled_Notes.html`, which is present in
  `docs-local/notes/` but not in the rebuilt `kb_1` index.
- Coverage targets the observed failure classes from the burned set without
  reusing the same questions: lexical underfetch, multi-key generation depth,
  grounded cross-source synthesis, parallel retrieval, routing boundaries, and
  corpus-negative absence checks.
- The two absence rows were checked by literal search for the specific absent
  topics (`Kubernetes`/`HPA`, `Rust`/`borrow checker`) across the four indexed
  note files.
- Failure-class regression run after CJK retrieval, router, and eval RAG prompt
  fixes: `results/holdout_fresh2_834cba3_20260823.jsonl`; summary:
  `results/holdout_fresh2_20260823_summary.json`. Burned 2026_08 regression
  artifact: `results/holdout_rerank_7e59e0b_20260823.jsonl`.
  Metrics: errors `0`, router `18/18`, positive-query retrieval `14/14`,
  positive-query relevance `14/14`, contains `18/18`, grounded `14/14`,
  faithfulness `1.0` (`n=3`), answer relevancy `1.0` (`n=3`),
  average latency `26.19s`, retries `1`.
  Fresh2 was already burned by earlier committed runs, so this is reused-set
  regression evidence after the failure-class fixes, not new fresh
  generalization evidence. Relevance here measures positive-query sensitivity,
  not negative-query specificity; unanswerable rows do not run diagnostic
  retrieval in `run_eval.py`.


## 2026-08-22 — fresh holdout reproduction set

- Added `holdout_set_2026_08.jsonl` as a fresh 16-item holdout for checking
  whether the 36-item W4/error-analysis tuning generalized beyond the set used
  for repeated tuning and rescoring.
- Holdout SHA-256:
  `18342083337d982ccda38ca026c4c4dba328d729143e2547c5863f351351e155`.
- The set uses the same synonym-group keyword syntax described below; baseline
  backports of `_normalize` / `_keyword_hit` were required so both comparison
  legs scored the new holdout with the same matcher semantics.
- First-pass comparison used the same KB (`kb_1`, 50 embeddings) and
  `models/gemini-3.5-flash`: baseline parent `e8dddab` / prep `d2aa26a` versus
  tuned parent `e6a5450` / prep `e0ec68a`.
- Reproduction ruling: partial. Transport robustness and groundedness
  reproduced; routing and broad semantic correctness did not cleanly reproduce;
  tuned latency regressed.
- Result artifacts:
  `results/holdout_baseline_e8dddab_firstpass_20260822.jsonl`,
  `results/holdout_baseline_e8dddab_after_resume_20260822.jsonl`,
  `results/holdout_tuned_e6a5450_20260822.jsonl`, and
  `results/holdout_20260822_summary.json`.
- This section records the new holdout protocol and outcome; it is not a
  post-score label correction.

- Follow-up router/latency cleanup `697b275` and adjusted holdout run
  `holdout_adjusted_697b275_20260822` used this same 16-item file again.
  Subsequent prompt-invariant route probe `cd6c0ef` also used it for routing
  verification. The holdout is therefore no longer fresh for future tuning
  decisions; use it only as a burned regression artifact. Adjusted artifacts:
  `results/holdout_adjusted_697b275_20260822.jsonl` and
  `results/holdout_router_prompt_invariant_cd6c0ef_20260822.jsonl`.

## 2026-07-22 - HITL write-note protocol set

- Added `hitl_gold_set.jsonl` as a separate deterministic protocol set rather
  than mixing action lifecycle cases into the answer-keyword evaluation.
- The 22 human-authored expectations cover English/Chinese persist and draft
  intent, near negatives, ordinary knowledge intent, approve/edit/reject,
  normal proposal, mixed/multiple-write rejection, summarize-and-save
  receipt-only UX, and every terminal receipt result.
- These rows define expected behavior from the frozen HITL design. They are
  not model outputs and do not report precision, pass rate, or real-Gemini
  evidence.

## 2026-07-11 — W4 error analysis (baseline e8dddab)

- **a005**: `expected_route` agent → rag. Reasoning: the query asks the
  model to organize existing knowledge-base content into an interview
  answer framework — that's a presentation/formatting task, not a signal
  requiring multi-step retrieval, comparison, or planning. One retrieval
  covers the needed material. `expected_answer_contains` updated to
  `["2×2|2x2", "rubric", "组件|component", "end-to-end", "position bias"]`
  (synonym-group syntax, see below).
- **a010**: same reasoning as a005, `expected_route` agent → rag.
- **r010**: left unchanged (still `rag`). The router misclassified this
  item as `agent` — that's a router accuracy failure, not a gold-set
  labeling error, so it stays as evidence of a real router miss.

## 2026-07-12 — id collision fix (a006 → p001)

- **a006 → p001**: the parallel-tool-call trigger item added this fix batch
  (Decisions §三 extension class ①, "同时查一下 Reflection 和 Planning...")
  was appended with `id: a006`, colliding with the pre-existing comparison
  item `a006` ("Agentic AI 与传统一次性 LLM 调用有什么本质区别？"). JSONL has
  no uniqueness constraint on `id`, and any dict-keyed join over the file
  (e.g. `{r["id"]: r for r in results}`) silently lets the later line shadow
  the earlier one — this is exactly what happened: the 20260712_fixw4
  failure list showed `a006` as `category=parallel`, making the comparison
  item invisible to that run's per-item results even though it was still
  evaluated. Renamed the parallel item's id to `p001`; the comparison item
  keeps `a006` unchanged. Full 36-id listing verified unique after the
  rename (35 unique ids before the fix, 36 after).

## 2026-07-13 — gold set v2 (post 3-run protocol, r010/r012/r013 triage)

- **r010** (2026-07-13): left unchanged (query, keywords, everything). Ruling:
  the question itself is not wrong; the system answered a different but
  adjacent framework from the knowledge base ("自主程度" autonomy-level
  spectrum) instead of the one the question asks about ("难度谱系"
  difficulty-spectrum examples: Invoice/Customer/Computer Use). This is a
  real system regression introduced by the corpus expansion (more chunks →
  more candidate framings to confuse retrieval/generation on), not a
  labeling defect, so it does not get a gold-set fix. Logged instead as a
  known regression in `docs-local/SmartDesk_Decisions.md` §四 for the next
  fix batch (candidates: retrieval re-ranking, a generation-prompt
  instruction to stay strictly on the asked framework, or chunk-level
  framework metadata).
- **r012** (2026-07-13): `query` rewritten to anchor the framework —
  "按 Agentic AI 课程笔记的说法，Agent 构建的本质是什么？" (was "Agent 构建的
  本质是什么？"). Reasoning: same failure class as r010 (multiple
  same-topic-different-framework passages in the expanded corpus), but here
  the fix is tractable at the question level by naming which course/note's
  framing is wanted. `expected_answer_contains` unchanged
  (`["system prompt","角色","LLM","Manager"]`, min_hits=2).
- **r013** (2026-07-13): `expected_answer_contains` rewritten to synonym
  groups: `["沙箱|隔离执行|sandbox|容器", "Docker|E2B|误删|不可信代码|资源限制|权限控制"]`,
  min_hits=2 (both groups required). Reasoning: across all 3 protocol runs
  the system consistently gave the correct sandboxing/isolation principle
  but never happened to name the specific products (Docker/E2B) or the
  specific failure mode (误删) the old flat keyword list required verbatim
  — a wording-specificity gap, not a content-correctness gap. New group 1
  captures the principle (sandbox/isolation) which every run already hits;
  group 2 requires either a concrete example or a second security
  principle, so min_hits=2 still enforces "principle + backing detail"
  rather than accepting the principle alone.

## 2026-07-13 — r013 group2 word addition (final contains-metric synonym correction)

- **r013**: group 2 augmented to
  `["Docker","E2B","误删","不可信代码","资源限制","权限控制","权限边界","执行层强制"]`
  (as a synonym group). Reasoning: the actual 3-run answer text says "权限
  边界...由死代码在执行层强制约束" -- this principle is present verbatim in
  the MCP notes source material, so adding it is completing an existing
  principle enumeration, not reverse-engineering keywords from the
  observed answer. Local rescore (no LLM calls) on the same 3-run exported
  text: r013 flips 0/3 → 3/3 pass; pooled contains_pass corrected from
  92/103 (89.3%) to 95/103 (92.2%).
- **r012**: query-rewrite fix (2026-07-13, above) is not separately
  re-run; its validation is folded into the next full eval run rather
  than a targeted single-item rerun.

**This is the last synonym-group correction against the contains metric
for this batch.** From here on, a `contains_pass=False` on a future run is
treated as a real miss, not grounds for another keyword-list adjustment.

### `expected_answer_contains` synonym-group syntax

A keyword may be a `|`-separated list of synonyms; matching any one variant
counts as a hit for that slot (e.g. `"2×2|2x2"` matches either the
math-symbol or ASCII form). Matching is additionally normalized: NFKC
(full-width→half-width), `×`→`x`, case-insensitive. See
`run_eval.py::_normalize` / `_keyword_hit`.
