# AGENTS.md

Repository rules for coding agents working in this repository. This is the
single public file that carries executable repository rules.

## Project

SmartDesk is an enterprise knowledge assistant: a FastAPI + ChromaDB + Gemini
RAG backend with an agentic answer path, SSE streaming, JWT auth, and a Vue 3
frontend.

- `README.md` is the entry point for setup, architecture, and product
  description.
- `docs/PROJECT_EVIDENCE.md` owns publicly verified outcomes.
- Read only the accepted spec under `docs/superpowers/specs/` that is
  relevant to the current task.

## Protected artifacts

- The `v1-baseline` tag and its code must not be modified; it is the fixed
  before/after comparison point.
- Do not modify, delete, or stage `backend/eval/results/*` (including
  `backend/eval/results/history.jsonl`) unless the task explicitly
  authorizes it. These are user-owned evaluation artifacts.

## Engineering rules

- Python 3.11+.
- All code, comments, API strings, and file content are written in English.
- Keep provider-facing LLM calls behind the existing client wrappers
  (`backend/llm/client.py` and `backend/gemini_client.py`); do not call
  provider APIs directly from business logic.
- Use a minimal diff. Preserve all behavior outside the accepted task scope;
  no drive-by refactors, renames, or formatting changes in unrelated files.
- A bug fix is not complete until its reintroduction would be caught by a
  test.
- Keep governance or documentation changes in commits separate from product
  changes so either can be reverted independently.

## Secrets and paid APIs

- Secrets are provided only through environment variables (`.env`); never
  commit them and never print their values. Logging a variable name is
  acceptable; logging its value is not.
- Before any real Gemini call or any request that may consume paid quota,
  inform the human of the planned scope.

## Verification

A completion claim requires observable evidence: run the relevant commands
and report their actual output.

Backend tests:

```bash
cd backend && pytest -q
```

Frontend tests:

```bash
cd frontend && node --test src/
```

Production build:

```bash
cd frontend && npm run build
```
