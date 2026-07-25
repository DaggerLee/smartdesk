# SmartDesk Public Agent Instruction Boundary Design

**Date:** 2026-07-25
**Status:** Approved by the human on 2026-07-25
**Scope:** Public instruction surface only; no product behavior changes
**Baseline:** `main@4853aff785498a43af6a88d73ee8cb901bd53c3d`

## Goal

Leave exactly one public file that carries executable repository rules, remove
private collaboration governance from the public surface, and keep every
repository-specific safety constraint that an external agent actually needs.

## Problem

A read-only audit of the public instruction surface found the following.

1. `AGENTS.md` and `CLAUDE.md` are both tracked and public, and they duplicate
   each other. `CLAUDE.md` is `AGENTS.md` minus one governance block; the
   remaining content is otherwise identical apart from four substitutions.
2. The two files conflict. Their `Collaboration Rules` bullet names a different
   executor in each file, so a reader cannot tell which public rule is
   authoritative.
3. The duplication has already drifted. Every rule added to `AGENTS.md` after
   `CLAUDE.md` was last touched is missing from `CLAUDE.md`, including the
   delivery governance, evaluation discipline, recovery discipline, credential
   handling, context handoff, and verified-delivery invariant blocks.
4. Both public files reference `docs-local/`, which `.gitignore` excludes. An
   external clone therefore receives mandatory instructions that point at files
   it cannot see, and the reference itself exposes the private layout.
5. The public surface carries a sprint plan with `W0` through `W5` week labels,
   a job-search timeline, agent role division, personal communication
   preferences, and general governance theory that is not specific to this
   repository.
6. `README.md` already owns installation, architecture, technology stack, and
   product description, and references neither instruction file. The duplicated
   setup material in the instruction files is therefore redundant.
7. `backend/agent/graph.py` contains one comment that attributes a repository
   constraint to `CLAUDE.md`, which will no longer own any rule.
8. `.gitignore` contains a public job-search phrase in a comment.

## Target structure

### AGENTS.md

`AGENTS.md` remains the single public file that carries executable repository
rules. The expected result is roughly 60 to 100 lines. Line count is an
expectation, not an acceptance threshold.

Retain:

- a short SmartDesk project description;
- the rule that the `v1-baseline` tag and its code must not be modified;
- a pointer naming `README.md` as the entry point for setup, architecture, and
  product description;
- a pointer naming `docs/PROJECT_EVIDENCE.md` as the semantic owner of public
  verified outcomes;
- the instruction to read only the accepted public spec relevant to the task;
- minimal diff, preservation of behavior outside the accepted task scope, and
  the rule that a bug fix is incomplete until a regression test would catch its
  reintroduction;
- Python 3.11+;
- English for code, comments, API strings, and file content;
- the single-module wrapper constraint for all LLM calls;
- secrets only through environment variables, never committed and never printed
  as values;
- the requirement to inform the human of scope before any real Gemini call or
  any request that may consume paid quota;
- the rule that `backend/eval/results/*` and
  `backend/eval/results/history.jsonl` must not be modified, deleted, or staged
  unless a task explicitly authorizes it;
- executable backend test, frontend test, and production build commands;
- the rule that a completion claim requires observable evidence;
- the rule that governance or documentation changes stay in commits separate
  from product changes.

Remove:

- every `docs-local/` reference;
- the sprint plan, all `W0` through `W5` labels, and the job-search framing;
- the Codex and Claude role division;
- personal communication preferences;
- capability routing, compaction, and fresh-task workflow rules;
- the `planned` / `prepared` / `running` / `verified` governance copy;
- Quick Start, directory tree, technology stack, database schema, v1
  architecture, and Docker material already owned by `README.md`;
- the `v2 refactor in progress` status phrase, which is no longer accurate.

#### Verification commands

The retained commands must be the portable form an external contributor can
run, not a local workaround:

```bash
cd backend && pytest -q
```

```bash
cd frontend && node --test src/
```

```bash
cd frontend && npm run build
```

A local WSL environment may require `python3 -m pytest -q` or
`node node_modules/vite/bin/vite.js build` because repository-tracked launchers
carry mode `0644` there. That workaround is an environment artifact rather than
a repository property, so it belongs to the private layer and must not enter
the public file.

### CLAUDE.md

`CLAUDE.md` becomes a thin pointer of two or three lines:

```markdown
# CLAUDE.md

See [AGENTS.md](AGENTS.md) for repository rules and [README.md](README.md)
for setup and architecture.
```

It must contain no additional rule, exception, summary, or duplicated content.
Any rule text reintroduced here recreates the competing authority this design
removes.

### .gitignore

Exactly one comment line changes:

```text
# Local job-search working docs (not for the public repo; docs/ reserved for public docs)
```

becomes a neutral equivalent such as:

```text
# Local working documents (not for the public repo; docs/ reserved for public docs)
```

No ignore pattern may change. The set of ignored paths must remain
byte-identical.

### backend/agent/graph.py

One existing comment currently attributes the single-client-module constraint
to `CLAUDE.md`. It is corrected to name the repository rule in `AGENTS.md`. No
code, control flow, runtime behavior, or other comment changes.

## Private ownership

The following is described here for completeness and must not be implemented in
this repository by this task.

- SmartDesk-specific collaboration detail belongs to the gitignored
  `CLAUDE.local.md`.
- General agent governance, including conflict resolution order, evidence
  states, evaluation methodology, recovery protocol, reporting format, and
  capability provenance routing, belongs to AI Manager OS, which already owns
  those rules.
- `docs-local/SmartDesk_Decisions.md` and `docs-local/CURRENT.md` remain
  private and unchanged.
- This task does not copy, create, migrate, or commit any private content. No
  rule is deleted from the project as a whole; each rule keeps exactly one
  owner, and only the public copy disappears.

## Scope

The implementation phase may modify only these four files:

1. `AGENTS.md`
2. `CLAUDE.md`
3. `.gitignore`
4. `backend/agent/graph.py`

This design document is an independent governance commit and is not counted in
the implementation file scope.

## Commit structure

The implementation phase uses three independent commits. No commit may be
amended, squashed, or merged into another boundary.

1. `docs: minimize public agent instructions`
   - `AGENTS.md`
   - the single `.gitignore` comment line
2. `docs: make Claude instructions a thin pointer`
   - `CLAUDE.md`
3. `docs: correct public instruction references`
   - the single `backend/agent/graph.py` comment

## Rejected alternative

**Keep two complete public rule files and synchronize them.**

Rejected because synchronization is a human convention rather than a structural
guarantee, and the evidence shows it has already failed: the two files
diverged, one stopped receiving updates, and their executor rule now openly
contradicts itself in public. This repository's own SSOT principle requires
removing the competing authority instead of adding reconciliation work, so
preserving both files would institutionalize the defect this design exists to
remove.

## Considered but not adopted

**Delete `CLAUDE.md` entirely.**

This is a legitimate option and is strictly better than synchronizing two
files. It was not adopted because `backend/agent/graph.py` references the
filename and because Claude Code treats that filename as the project
instruction convention. A two-line pointer removes the duplicate authority at
negligible cost while keeping both references resolvable. If the human later
prefers one fewer file, deletion remains available and requires only the same
`graph.py` comment correction already planned here.

## Risks and controls

**Risk:** over-reducing `AGENTS.md` could strip repository-specific safety
constraints, so an external agent might modify the `v1-baseline` tag, stage
user eval artifacts, print secret values, or call a paid API without notice.

**Control:** only duplicated or private-governance content is removed. Every
repository-specific constraint is explicitly listed in the retain set above,
and acceptance verifies each one survives. The removal criterion is "this rule
has another owner or is not about this repository", never "this file is long".

**Risk:** a future contributor reintroduces rules into `CLAUDE.md`, recreating
the dual authority.

**Control:** the thin-pointer content is fixed by this design, and acceptance
requires `CLAUDE.md` to contain only a heading and two links.

**Risk:** removing `docs-local/` references could make the private handoff
protocol look abandoned.

**Control:** the private layer already exists and is unchanged; the fresh-task
startup rule moves to `CLAUDE.local.md`, next to the private files it names.

## Non-goals

- Product functionality, configuration, dependency, or test changes.
- `README.md` and `docs/PROJECT_EVIDENCE.md` content changes.
- GitHub About, Topics, Contributors, or other repository metadata.
- Cleanup of existing local or remote branches.
- Implementing the private-document migration in any repository.
- Any real Gemini or paid-quota request.

## Acceptance

1. `AGENTS.md` is the only public file carrying executable repository rules.
2. `CLAUDE.md` contains only a heading and two links, with no rule text.
3. Neither `AGENTS.md` nor `CLAUDE.md` references `docs-local/`.
4. The public rule files contain no sprint plan, no `W0` through `W5` label, no
   job-search or 2026 recruiting phrasing, and no Codex/Claude executor
   conflict.
5. `.gitignore` contains no job-search phrasing, and its ignore patterns are
   byte-identical to the baseline.
6. `backend/agent/graph.py` changes in exactly one comment, with no code or
   behavior change.
7. `README.md`, `docs/PROJECT_EVIDENCE.md`, product behavior, configuration,
   dependencies, tests, images, and eval artifacts are unchanged.
8. The backend test, frontend test, and production build commands published in
   `AGENTS.md` are genuinely executable.
9. Every retained constraint listed in the `AGENTS.md` retain set is present.
10. `git diff --check` passes.
11. The tracked worktree is clean after each commit.
12. The 50 untracked `backend/eval/results/*` artifacts and the tracked
    `backend/eval/results/history.jsonl` are unmodified, undeleted, and
    unstaged.
13. No Gemini request is made at any point.
14. The three implementation commits exist with the planned boundaries and none
    was amended or squashed.
