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

### `expected_answer_contains` synonym-group syntax

A keyword may be a `|`-separated list of synonyms; matching any one variant
counts as a hit for that slot (e.g. `"2×2|2x2"` matches either the
math-symbol or ASCII form). Matching is additionally normalized: NFKC
(full-width→half-width), `×`→`x`, case-insensitive. See
`run_eval.py::_normalize` / `_keyword_hit`.
