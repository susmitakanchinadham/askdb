# PLAN

- [x] **Slice 1: Scaffold, semantic layer loader, read-only connection**
  - Files: `pyproject.toml`, `src/askdb/{__init__,main,config,db}.py`, `config/databases/cloudmetrics.yaml` (copied unchanged from `data/cloudmetrics.yaml`), `config/validation/cloudmetrics.yaml` (copied from `data/cloudmetrics_eval.yaml`), `tests/unit/test_config.py`, `tests/integration/test_readonly.py`
  - Config format (SPEC 4): `name`, `display_name`, `path` (`${CLOUDMETRICS_DB_PATH}`), `calendar.fiscal_year_start_month`, `description` (verbatim), `tables[]` with `columns[]` (`name`, `type`, `primary_key`, `references`, `description`). Loader renders `description` + `CREATE TABLE` DDL with comments as the model-facing text; version = hash of the file.
  - Verify: loader parses `data/cloudmetrics.yaml` and the rendered text matches a snapshot; `references` resolve to real columns; INSERT/DROP/ATTACH fail at engine level; schema-drift test (yaml column missing from a temp DB is removed from the model-facing text and reported); `GET /health`.

- [x] **Slice 2: SQL validator (AST only)**
  - Files: `src/askdb/validator.py`, `tests/unit/test_validator.py`
  - Verify: injection corpus (stacked statements, comments, CTE/subquery tricks, forbidden functions, disallowed column) is rejected; all golden `expected_sql` pass the validator.

- [x] **Slice 3: Executor, authorizer, limits and pagination**
  - Note from slice 2: `validator.validate_sql(sql, allowed)` returns `ValidationResult` (`ok`, `code`, `message`, `sql`, `tables`, `columns`). Execute `result.sql` (regenerated from the checked tree, stars expanded, comments removed), never the original text. `allowed` is `{table: columns}` from `validator.allowed_from_layer(layer)`. The authorizer should allow exactly `result.tables` and `result.columns` as a cross-check.
  - Note from slice 1: `PRAGMA query_only` does not stop `ATTACH`, which creates a file on disk. `db.open_readonly` now sets `SQLITE_LIMIT_ATTACHED` to 0 (tested). The authorizer must still deny ATTACH/PRAGMA/functions itself; reuse `open_readonly` rather than opening connections elsewhere.
  - Files: `src/askdb/executor.py`, `src/askdb/authorizer.py`, `tests/integration/test_executor.py`, `tests/integration/test_authorizer.py`, `tests/unit/test_pagination.py`
  - Verify: authorizer denies disallowed tables/columns and non-SELECT actions; every golden `expected_sql` returns rows; runaway query is interrupted at the deadline; over-1,000-row result truncates and is flagged; page 2 is stable and disjoint from page 1; concurrency cap holds.

- [ ] **Slice 4: Planner with fake LLM client, first end-to-end ask**
  - Note from slice 3: run answers with `Executor(db_path).execute(validated, page=1)` (`executor.py`); it returns a `Page` (`columns`, `rows`, `total` capped at 1,000, `truncated`, `has_more`, `as_of`) or raises `QueryTimeout`, `TooBusy`, `ExecutionError`. Their messages are for logs only; map each to business wording in the API layer. One `Executor` instance owns the concurrency cap, so create it once at startup.
  - Note from slice 2: on a rejection feed `result.code` and `result.message` back to the model for the single retry; they are written for the model and must never reach the user (they say SELECT, table, column). Only a small set of functions is allowed (see `_SAFE_TYPED` / `_SAFE_ANONYMOUS` in `validator.py`); `printf`, `hex`, `random`, `json_*`, recursive CTEs and bare `*` in non-COUNT places are refused, so tell the model the allowed list in the prompt.
  - Note from slice 1: use `config.render_model_text(layer)` as the prompt body and `main.load_checked_layer()` to get a layer already pruned against the live database (`layer.issues` lists what was dropped; `layer.version` is the glossary version for logs). The golden set loads from `config/validation/cloudmetrics.yaml`.
  - Prompt input is exactly the semantic layer's `description` plus the rendered table DDL (SPEC 4), the question and prior interpretations.
  - Files: `src/askdb/{planner,llm,prompts}.py`, `src/askdb/api/ask.py`, `tests/unit/test_planner.py`, `tests/validation/test_golden.py`, `tests/validation/recorded/`
  - Verify: plan, clarify, refuse, retry-once, double-failure and outage unit tests; no result rows ever reach the LLM client; golden clarification (`revenue_ambiguous_mrr_or_invoices`, `recent_churn_relative`) and declined (`small_talk`, `data_change_request`, `injection_attempt`) outcomes; `uv run pytest tests/validation` passes offline on recorded outputs; opt-in live run against Claude.

- [ ] **Slice 5: Answer composer and wording lint**
  - Note from slice 3: the executor raises exceptions with technical text (`QueryTimeout` -> suggest a shorter period, `TooBusy` -> try again shortly, `ExecutionError` -> generic failure); the copy-lint must cover the strings chosen for these, and the "more than 1,000 results" message driven by `Page.truncated`.
  - Files: `src/askdb/{composer,number_check,strings}.py`, `tests/unit/test_composer.py`, `tests/unit/test_copy_lint.py`
  - Verify: wrong LLM sentence falls back to table-only; empty, NULL and divide-by-zero cases; copy-lint fails on any banned word across user-facing strings and API errors; golden cases produce sentences matching result cells.

- [ ] **Slice 6: Roles, access control, audit log**
  - Note from slice 2: pass the role-filtered `{table: columns}` as `allowed`. A hidden column is reported as `Code.UNKNOWN_COLUMN` and `SELECT *` silently expands to visible columns only, so the neutral role refusal must be chosen by the caller, not read from the validator message.
  - Blocked on SPEC open item 8: the database YAML has no roles or sensitivity fields, so decide where role allowlists live (separate roles file vs extending the YAML) before starting. The database YAML is not changed on assumption.
  - Files: role allowlist config (location per item 8), `src/askdb/{roles,audit}.py`, `tests/integration/test_access.py`, `tests/unit/test_audit.py`
  - Verify: per role, forbidden columns never appear in prompt, SQL or results; hidden-column question gets a neutral refusal; audit row written for answer, clarify, refuse and error with no result values.

- [ ] **Slice 7: Follow-ups, chip re-run, "This looks wrong", rate limit**
  - Note from slice 3: the executor only has a global concurrency cap. "One running query per user" and the requests-per-minute limit (SPEC 5.1 item 6) still need a per-user guard in front of `Executor.execute`.
  - Files: `src/askdb/{session,feedback}.py`, planner/prompts and `api/` extensions, `tests/integration/test_followup.py`, `tests/unit/test_session.py`
  - Verify: golden follow-up `mrr_by_plan_followup_industry` passes; follow-up under a restricted role cannot reach hidden columns; session expiry (30 min) and "New question" reset; chip change re-runs through the same validator; feedback row holds question, plan, SQL, glossary version and row count; per-user rate limit.

- [ ] **Slice 8: React web UI**
  - Files: `web/` (`App`, `Chat`, `AnswerCard`, `ResultTable`, `Chips`, `Clarify` components, API client, `web/tests/`), `tests/e2e` smoke
  - Verify: component tests for card, pagination, chips re-run and clarify choices; axe accessibility check; manual run against `data/cloudmetrics.db` with three golden questions plus one refusal.
