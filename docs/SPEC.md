# AskDB: Specification

Business users ask questions of one database in plain English and get a short plain-English answer plus a readable table. Source: `docs/one-pager.md` and the interview decisions below.

## 1. Goals and non-goals

**Goals**
- Non-technical staff (managers, sales, ops, finance) get answers with no SQL knowledge and no one to fall back on.
- Supported questions: lookups (one order's status, a customer's details) and aggregates (sales by region last quarter, new customers this month).

**Never happens**
1. Data is changed or deleted.
2. A confident wrong answer is shown. Ambiguous or unanswerable questions get a plain-language clarification or refusal.
3. A query hangs or loads the database.

**Out of scope (v1)**: dashboards, saved/scheduled reports, export/sharing, multiple databases, row-level security, learning from user feedback.

## 2. Decisions summary

| Area | Decision |
|---|---|
| Database | SQLite only: one database file, opened read-only |
| Meaning of data | Admin-curated semantic layer: one YAML per database (`data/cloudmetrics.yaml` format); undefined terms are refused and logged |
| Read-only | Enforced by opening the file read-only (`mode=ro`) with `PRAGMA query_only`, plus an authorizer callback and SQL validator (see 5.1) |
| Trust | Every answer states its interpretation; glossary defaults with one-click change |
| Access | Per-role table/column allowlists; no row-level rules in v1 |
| Results | Paginated via LIMIT/OFFSET with a hard row ceiling |
| Summary | Numbers injected from result cells; validator before display |
| LLM data | LLM receives only question, glossary/schema and conversation text. It never receives result rows |
| Conversation | Follow-ups within a session; full audit log |
| Quality | In-app "This looks wrong" button (golden set recommended, see Open items) |
| Platform | Internal web app behind company SSO, chat-style UI |

## 3. Architecture

Components:
1. **Web UI** (React): chat thread, example questions, answer cards.
2. **API service**: auth, session/conversation state, orchestration. Python (FastAPI) assumed; Next.js is acceptable if the team prefers TypeScript. Choose one before build.
3. **Semantic layer**: one YAML file per database in `config/databases/` (format in section 4, reference: `data/cloudmetrics.yaml`), edited by the admin. It holds the connection, calendar, a free-text description with the business rules, and the table/column definitions.
4. **Query planner (LLM)**: turns question + glossary + session context into a structured query plan and SQL.
5. **Validator/executor**: validates SQL, runs it against the SQLite file with limits.
6. **Answer composer**: builds the sentence from a template with numbers from result cells; the LLM only chooses phrasing (no numbers).
7. **Audit log store**: separate from the analytics database.

Request flow:
1. Authenticate user, resolve role.
2. Build the prompt from the **role-filtered** semantic layer (`description` plus the tables and columns the role may see) only (tables/columns the role may not see are never shown to the LLM, so they cannot be referenced or leaked in clarifications).
3. LLM returns one of: `answer_plan` (interpretation + SQL), `clarify` (question + options), or `refuse` (reason code).
4. Validate SQL (5.1). Reject and retry once with the validator error; on second failure, refuse politely.
5. Execute with limits, fetch page 1 plus `COUNT`.
6. Compose answer, validate numbers (5.3), return card.

## 4. Semantic layer

One YAML file per database, in the format of `data/cloudmetrics.yaml` (copied to `config/databases/`). Nothing else describes the data to the model.

```yaml
name: cloudmetrics
display_name: CloudMetrics SaaS Analytics
path: ${CLOUDMETRICS_DB_PATH}      # opened read-only (mode=ro)
calendar:
  fiscal_year_start_month: 1
description: |                     # free text, sent verbatim
  ...
tables:
  - name: customers
    description: ...
    columns:
      - { name: customer_id, type: INTEGER, primary_key: true, description: "..." }
      - { name: plan_id, type: INTEGER, references: plans.plan_id }
```

- **Connection**: `name`, `display_name`, `path` (environment variable expanded at load), `calendar.fiscal_year_start_month`.
- **`description`**: the only home for business rules. It states the grain of each table, how tables join, metric definitions (MRR, revenue, churn rate, average resolution time, customer count, ARPU), status conventions and NULL rules, SQLite date-handling idioms, and which table answers what. It is sent to the model verbatim.
- **`tables`**: each has `name`, `description` and `columns`. A column has `name`, `type` (INTEGER, TEXT, REAL, DATE, DATETIME), optional `primary_key`, optional `references: table.column` and a `description`. Dates are ISO text; the format is stated in the column description.
- **Model-facing text** is exactly `description` plus `tables` rendered as `CREATE TABLE` DDL with the descriptions as comments. The model sees no other schema information.
- Fields this format does not have: per-entry synonyms, sensitivity tags, allowed roles, `requires_time_filter`, a separate metrics section and company timezone. Where the spec mentions them, they are not part of the database file; role allowlists need a home (open item 8).

Behaviours:
- A term the description does not define: the planner refuses with "I can't answer that reliably yet" and the request is logged as a **glossary miss** for the admin.
- Defaults (calendar year, which revenue measure, what "active customer" means) are the ones written in `description` and `calendar`. If a question needs a period the description cannot resolve (e.g. "last month" when the data ends in November 2025), the planner asks "Which period?".
- The glossary version recorded with each logged answer is a hash of the YAML file contents.
- A load-time check compares `tables`/`columns` with the live schema (dropped or renamed columns, wrong types). A broken table or column is removed from the model-facing text and raised to the admin rather than failing silently; metrics that depend on it cannot be answered.

## 5. Safety and correctness

### 5.1 Read-only and load protection (defence in depth)
1. SQLite has no users or roles, so protection comes from the connection itself. The app opens the database file with `mode=ro` (URI `file:...?mode=ro`), so writes fail at the engine level. Use a **copy or snapshot** of the live file (or WAL-mode read-only access) so analytics reads never block the system that writes the data. The OS account running AskDB has read-only file permissions.
2. Every connection also sets `PRAGMA query_only = ON`, and `ATTACH`, `load_extension` and `PRAGMA` statements are disabled.
3. A SQLite **authorizer callback** (`sqlite3_set_authorizer`) allows only `SELECT`/`READ` on allowlisted tables and columns and denies everything else (this is the enforcement point for role allowlists, since there are no database-level grants). In addition, a SQL parser/validator accepts exactly one `SELECT` statement and rejects DDL/DML, multiple statements, `ATTACH`, `PRAGMA`, `load_extension`, `readfile`/`writefile` and similar functions, and expensive constructs such as unbounded recursive CTEs (validated against the parsed AST, not text matching).
4. SQLite has no `statement_timeout`. The ~10 second limit is enforced with a **progress handler** (`sqlite3_progress_handler` / `Connection.interrupt()`) that aborts the query once the deadline passes, plus a busy timeout for lock waits. On timeout the user sees a business-language message suggesting narrowing (e.g. shorter period). Timeout is configurable by admin.
5. Row ceiling: queries are wrapped with `LIMIT`; absolute ceiling 1,000 rows per question.
6. Short-lived read-only connections (one per request) with a small global concurrency cap; one running query per user; per-user requests-per-minute limit.
7. Generated SQL is never shown to users.

### 5.2 Pagination
- Page size 25. Pages are stateless: re-run with deterministic `ORDER BY` (a tie-breaker on a unique key is added) and `LIMIT/OFFSET`.
- Total count computed once per question and cached for the session's card.
- If the total exceeds 1,000, show up to the ceiling and say "There are more than 1,000 results; try narrowing by …" with suggestions. Aggregates are always computed in the database, never from the truncated set.
- Data can change between pages; the card shows "as of <time>" and a refresh control. Accepted limitation.

### 5.3 Summary sentence integrity
- The sentence is a template filled with values from result cells; the LLM only selects/phrases the wording around them.
- Validator: every number, date and name in the sentence must match a value in the result (accounting for formatting and rounding rules defined per unit). Mismatch → show table only with a neutral note ("Here are the figures").
- Empty result: say "No matching records found" and restate the interpretation. Do not imply data doesn't exist if a filter might be too narrow; suggest broadening.
- Null/missing values display as "Not recorded," not zero.

### 5.4 Ambiguity and confidence
- Every answer card shows an **interpretation line** in business terms, e.g. "Net sales, fiscal Q3 (Jul–Sep), by billing region," with chips to change period, measure or grouping; changing a chip re-runs the query.
- Defaults come from the semantic layer `description`. Ask a clarifying question only when no default exists or two readings are about equally plausible. Clarifications offer 2–4 choices plus free text.
- Refuse when: term undefined, required data not permitted for the role, question is outside lookup/aggregate scope (e.g. forecasts, "why" questions), or validation fails twice.
- The model is not allowed to guess missing metrics; its output schema forces `answer_plan`, `clarify` or `refuse` with a reason code.

### 5.5 Prompt injection and untrusted data
- Question text and data values are untrusted. Result data is never sent to the LLM (so stored text cannot inject instructions).
- Questions trying to override rules are handled as refusals; the validator is the real control.

## 6. Access control

- Authentication via company SSO; users map to **roles** (admin-managed mapping).
- Each role has a table/column allowlist in the semantic layer. Sensitive columns (SSN, card, email, phone, etc.) are excluded from most roles.
- No row-level filtering in v1. Accepted risk: any permitted user can look up any customer within their allowed columns. Mitigations: per-user lookup rate limit, bulk results bounded by the 1,000 row ceiling, full audit log, and admin review of lookup volume.
- The allowlist is enforced both by prompt filtering and by the SQL validator; for defence in depth, queries run against a database file or views containing only allowed tables and columns where practical (SQLite cannot restrict access per user).
- **Before connecting real data**: admin signs off on the role allowlists and the PII lookup policy.
- Asking about a field outside their role gets a neutral refusal ("That information isn't available to your role") that does not reveal the data's existence beyond that.

## 7. Conversation

- Follow-ups ("and for last month?", "just the West region") are supported within a session. The planner receives the prior **interpretations** (structured plan, not result rows).
- Follow-ups re-run through the same validation and role filtering; context cannot widen permissions.
- Session expires after inactivity (default 30 minutes); a "New question" button clears context.
- If a follow-up is ambiguous against context, ask rather than assume.

## 8. UI/UX

- Main screen: chat-style thread, text input, and 4–6 admin-curated **example questions** that match the user's role.
- Answer card: (1) short answer sentence, (2) interpretation line with change chips, (3) paged table with business column names, formatted numbers/currency/dates, (4) "as of" time and row count, (5) "This looks wrong" button.
- Loading state with progress text in business language ("Looking that up…"); a cancel button; hard stop at the timeout.
- Errors, refusals and clarifications use business terms only: never "SQL," "query," "table," "column," "timeout," "schema," or stack traces. Words like "search" and "records" are preferred. A copy-lint test enforces a banned-word list on all user-facing strings.
- Accessibility: keyboard operable, screen-reader labels on tables and chips, WCAG AA contrast.
- Desktop-first; usable on tablet widths.
- "This looks wrong": one click plus optional free-text note. Captures the full context (question, plan, SQL, glossary version, result metadata) and notifies the admin. The user sees "Thanks, we'll look into it." It does not change the glossary automatically.

## 9. Audit and operations

- Audit log per request: user, role, timestamp, question, outcome (answer/clarify/refuse/error), plan, generated SQL, glossary version, row count, duration, validator result. Result values are **not** stored (only counts), to avoid duplicating sensitive data; access to the log is restricted to admins.
- Admin views: glossary misses, refusal reasons, "looks wrong" flags, slow/timeout queries, per-user lookup volume.
- Retention for logs set by company policy (default 1 year, configurable).
- Monitoring: LLM failure/latency, concurrency saturation, and snapshot age. If the snapshot is old, show an "as of" warning.
- LLM provider outage → friendly "I can't answer right now, please try again shortly"; no fallback guessing.

## 10. LLM usage

- Inputs: role-filtered glossary, conversation interpretations, the question. **No row data**.
- Output: structured JSON (`answer_plan` | `clarify` | `refuse`), not free text. SQL is validated before use.
- Data boundary: only schema/glossary text and user question leave the company network; confirm with security that glossary content (which may reveal business structure) is permitted to go to the provider.
- Model version pinned; changes go through the evaluation process below.

## 11. Testing and quality

- Unit tests: validator (injection, multi-statement, forbidden functions, disallowed columns, CTE and subquery tricks), answer-number validator, pagination ordering, banned-word lint.
- Integration tests against a test database opened read-only, confirming writes fail at the engine level and the authorizer blocks disallowed tables and columns.
- Load/timeout tests proving the timeout and concurrency caps hold.
- Access tests per role: forbidden columns never appear in prompts, SQL or results.
- Golden set: `config/validation/<database>.yaml` (reference `data/cloudmetrics_eval.yaml`). Each case has `id`, `question`, `category`, and either `expected_sql` (pass when the generated SQL returns the same result set) or `expected_outcome: clarification | declined`; optional `conversation` (prior turns with SQL), `today` (clock for relative dates) and `notes`. Run by `uv run pytest tests/validation`; never loaded at startup or sent to the model.
- Accuracy: collect "looks wrong" flags and review. A golden set is strongly recommended (see Open items).

## 12. Edge cases to handle explicitly

- Duplicate customer names in lookups → clarify with distinguishing details the role may see.
- Order/customer not found → say so, suggest checking the number.
- Time zones and period boundaries (inclusive/exclusive end dates), partial current periods ("this month" is month-to-date and says so).
- Currency and multiple currencies: do not sum across currencies without a defined conversion; refuse or split by currency.
- Soft-deleted or test records excluded via rules written in the `description`.
- Percentages/averages computed in the database; division by zero shown as "Not applicable."
- Result row count 0 on aggregates vs. true zero.
- Joins that fan out and double-count: the `description` declares each table's grain and join path; validator-checked templates preferred for key metrics.
- Very long text fields truncated in tables with expand.
- Concurrent edits to the glossary while sessions are active: sessions pin the version at question time.

## 13. Open items

1. **Stack**: FastAPI + React or Next.js. Decide before build. (Database engine is fixed: SQLite.)
   - SQLite specifics to confirm: how the file is refreshed (snapshot schedule) and how large it is, since a single-file database has no replica and its date/time types are stored as text or numbers, so each date column's format is declared in its column description.
2. **Quality gate**: only the "looks wrong" button was chosen. Recommended addition: an admin-owned golden question set (~50–100 questions with verified answers) run on every prompt/model/glossary change, since non-technical users rarely report errors.
3. **Pagination vs. load**: pagination was chosen over a capped single view; the 1,000 row ceiling and rate limits are this spec's proposed compromise. Confirm.
4. **Guardrails not selected**: `EXPLAIN QUERY PLAN` cost pre-check and required-date-range on big tables were not chosen as global rules; the YAML format has no per-table time-filter flag, so period handling rests on the `description` rules. Confirm whether cost checks are wanted.
5. **Row-level security**: accepted as out of scope; revisit if lookups of customer data prove sensitive.
6. **Logging retention** and who the named admin(s) are.
7. Provider data-handling approval for glossary text sent to the LLM.
8. **Role allowlists**: the database YAML has no roles or sensitivity fields. Decide where per-role table/column allowlists live (separate roles file vs extending the YAML) before access control is built.
