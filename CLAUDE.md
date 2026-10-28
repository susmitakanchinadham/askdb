# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# AskDB

Business users ask questions of one SQLite database in plain English and get a short answer plus a readable table.
Read-only, role-aware and audited. Lookups and aggregates only (see `docs/one-pager.md`).

## Source of truth

- `docs/SPEC.md` is the source of truth. If code and spec disagree, fix the code or update the spec in the same change; never silently drift.
- `docs/PLAN.md` tracks progress. Update it when a task starts, finishes or is re-scoped. (It does not exist yet; create it before the first build task.)
- SPEC section 13 lists open decisions. Do not settle one (stack, retention, cost checks) without asking.

## Stack (assumed, pending SPEC 13.1)

Python 3.12 + FastAPI backend managed with `uv`, React frontend, SQLite (stdlib `sqlite3`), pytest, ruff.
The `uv`/pytest hints in `data/cloudmetrics_eval.yaml` point this way; confirm before scaffolding. If Next.js is chosen, update this file.

## Commands (planned; none exist until the project is scaffolded)

```
uv sync                                  # install
CLOUDMETRICS_DB_PATH=./data/cloudmetrics.db uv run uvicorn askdb.main:app --reload   # run API
uv run pytest                            # all tests
uv run pytest tests/path/test_x.py::test_name   # one test
uv run pytest tests/validation           # eval set (data/cloudmetrics_eval.yaml)
uv run ruff check . && uv run ruff format --check .   # lint
```

## Folder layout (planned)

```
docs/        SPEC.md, PLAN.md, one-pager.md
config/databases/    semantic layer per database (see data/cloudmetrics.yaml)
config/validation/   golden question sets (see data/cloudmetrics_eval.yaml)
data/        sample SQLite db + sample configs
src/askdb/   API, planner, validator, executor, composer, audit
tests/       unit, integration, validation
web/         React UI
```

## Guardrails

- **Read-only SQL.** Open SQLite with `mode=ro` and `PRAGMA query_only=ON`, add an authorizer callback, and accept exactly one `SELECT` validated on the AST (SPEC 5.1). Never build a path that writes.
- **Row cap and timeout.** Hard ceiling of 1,000 rows, pages of 25, about 10 s timeout via progress handler. Do not raise or bypass them.
- **No result rows to the LLM.** Only the question, role-filtered glossary and prior interpretations.
- **Business wording only** in user-facing text: no "SQL", "query", "table", "column", "timeout", "schema". Never show generated SQL.
- **Secrets.** Never commit secrets, API keys or `.env` files. Never read `.env`; ask the user for any value you need.

## Conventions

- Every change ships with tests: unit tests for logic, plus integration or validation tests when it touches the validator, executor, access control or prompts. Run tests and lint before declaring done.
- Bug fixes start with a failing test that reproduces the bug.
