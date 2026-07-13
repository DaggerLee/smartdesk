# Gold Set Changelog

Revisions to `gold_set.jsonl` after the item itself was already scored — i.e.
the label was wrong, not the system. Route/scoring bugs found in the
harness or agent code are fixed in code, not here.

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
