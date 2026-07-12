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

### `expected_answer_contains` synonym-group syntax

A keyword may be a `|`-separated list of synonyms; matching any one variant
counts as a hit for that slot (e.g. `"2×2|2x2"` matches either the
math-symbol or ASCII form). Matching is additionally normalized: NFKC
(full-width→half-width), `×`→`x`, case-insensitive. See
`run_eval.py::_normalize` / `_keyword_hit`.
