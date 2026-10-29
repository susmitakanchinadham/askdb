# Run AskDB locally

Commands were run on Windows (Git Bash) against this repository, except where a section says otherwise (the live evaluation run, adding a second database, and the `ASKDB_API`/`ASKDB_DEV_*` overrides). Run them from the repository root unless a step says otherwise.

## What this guide does not cover

Three things you might expect do not exist in the code yet, so there is nothing to document or verify:

- **No user accounts.** There is no "create the first admin user" step. The API trusts two headers, `X-Askdb-User` and `X-Askdb-Role`, which the company SSO proxy is meant to set (SPEC section 6, open item 9). Locally, the Vite dev server sets them for you. See [Identity](#identity).
- **No admin page.** Databases are added by editing YAML files and restarting the API. See [Add a database](#add-a-database).
- **No `ASKDB_CONFIG_DIR`.** Configuration is read from files named by `ASKDB_CONFIG` and `ASKDB_ROLES`. See [Environment variables](#environment-variables).

## Prerequisites

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- Node.js and npm (for the web UI)

```
uv sync
cd web && npm install
```

## Environment variables

| Variable | Needed | Meaning |
|---|---|---|
| `CLOUDMETRICS_DB_PATH` | yes | Path to the SQLite file. The config refers to it as `${CLOUDMETRICS_DB_PATH}`. Use `./data/cloudmetrics.db` for the sample. |
| `ANTHROPIC_API_KEY` | for real answers | Read when the API starts. Without it every question gets "I can't answer right now". Never commit it; `.env.example` lists the name only. |
| `ASKDB_CONFIG` | no | Semantic layer file. Default `config/databases/cloudmetrics.yaml`. |
| `ASKDB_ROLES` | no | Role allowlist file. Default `config/roles/cloudmetrics.yaml`. |
| `ASKDB_AUDIT_PATH` | no | Audit and feedback database, created on first use. Default `data/audit.db`. |
| `ASKDB_MODEL` | no | Model name. Default `claude-sonnet-5-5`. |
| `ASKDB_RATE_PER_MINUTE` | no | Per-user question limit. |
| `ASKDB_LLM=replay` | dev only | Answers the golden-set questions from recorded replies, with no key and no network. Any other question is declined. |
| `ASKDB_LIVE=1` | opt-in | Makes the evaluation set call the real model. |

Git Bash:

```
export CLOUDMETRICS_DB_PATH=./data/cloudmetrics.db
export ANTHROPIC_API_KEY=<your key>
```

PowerShell:

```
$env:CLOUDMETRICS_DB_PATH = "./data/cloudmetrics.db"
$env:ANTHROPIC_API_KEY = "<your key>"
```

## Start the API

```
uv run uvicorn askdb.main:app --reload
```

Check it:

```
curl localhost:8000/health
```

Expected: `{"status":"ok","database":"CloudMetrics SaaS Analytics","version":"...","issues":[]}`. A non-empty `issues` list means the config names a table or column the database does not have.

No key yet? Add `ASKDB_LLM=replay` before the command to try the app with canned answers (Git Bash: `ASKDB_LLM=replay uv run uvicorn askdb.main:app --reload`).

## Start the front end

In a second terminal:

```
cd web
npm run dev
```

Open http://localhost:5173. The dev server proxies `/api` to the API on port 8000 and adds the identity headers `dev` / `admin`. Try "What is our current total MRR?"; with `ASKDB_LLM=replay` that returns `$22,344.11`.

To point at another API, or act as another user or role:

```
ASKDB_API=http://127.0.0.1:9000 ASKDB_DEV_USER=maria ASKDB_DEV_ROLE=support npm run dev
```

Roles come from `config/roles/cloudmetrics.yaml` (`admin`, `analyst`, `support`).

## Identity

Calling the API directly needs both headers; without them the API returns 403:

```
curl -X POST localhost:8000/ask \
  -H "X-Askdb-User: dev" -H "X-Askdb-Role: admin" \
  -H "Content-Type: application/json" \
  -d '{"question":"What is our current total MRR?"}'
```

Anyone who can reach the API can claim any role, so never expose it directly. In production it must sit behind the SSO proxy.

## Add a database

There is no admin page. To add one by hand:

1. Copy `config/databases/cloudmetrics.yaml` to `config/databases/<name>.yaml`. Fill in `name`, `display_name`, `path` (for example `${MY_DB_PATH}`), `description` (the business rules) and `tables`.
2. Copy `config/roles/cloudmetrics.yaml` to `config/roles/<name>.yaml` and set what each role may see.
3. Set the path variable, then start the API with both files:

```
MY_DB_PATH=./data/my.db ASKDB_CONFIG=config/databases/<name>.yaml ASKDB_ROLES=config/roles/<name>.yaml uv run uvicorn askdb.main:app --reload
```

The API serves one database per process. The config format is in SPEC section 4. This workflow was not run end to end because only CloudMetrics exists; the same variables are what the CloudMetrics run above uses by default.

## Run the evaluation set

The golden questions are in `config/validation/cloudmetrics.yaml`.

Offline, against recorded replies (no key, no network):

```
uv run pytest tests/validation
```

Expected: `53 passed`.

Live, against the real model (needs `ANTHROPIC_API_KEY`, makes network calls):

```
ASKDB_LIVE=1 uv run pytest tests/validation
```

Add `ASKDB_RECORD=1` to overwrite `tests/validation/recorded/` with the live replies. The recorded files are currently seeded from the golden set, not from a live run. The live run has not been executed here because no key was available.

## Everything else

```
uv run pytest                                          # all backend tests
uv run ruff check . && uv run ruff format --check .    # lint
cd web && npm test                                     # UI tests
cd web && npm run build                                # typecheck and production build
```
