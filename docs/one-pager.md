# AskDB: One-Pager

Business users ask questions of a database in plain English and get plain answers.

## Who it's for
Non-technical staff: managers, sales, ops and finance people with no SQL or data skills. They have no one to fall back on, so the app must work on its own.

## What they ask
- **Lookups:** the status of one order, or a customer's details.
- **Aggregates and totals:** sales by region last quarter, or new customers this month.

## What they see
- A short plain-English answer stating the result.
- A readable table of the figures behind it.

## What must never happen
- **Changing or deleting data.** The app only reads.
- **Confident wrong answers.** If a question is ambiguous or can't be answered reliably, the app says so or asks a clarifying question in plain language.
- **Runaway or slow queries.** No question may hang or put load on the database.

## Out of scope (for now)
- Dashboards and saved or scheduled reports.
- Exporting and sharing results.
- Combining multiple databases. It answers from one database only.

## Language
Answers, errors and clarifications use business terms. No technical or database vocabulary.

## Open question
Who may see which data? This was not decided. Non-technical users are less likely to notice or report data they shouldn't have, so it needs an answer before real data is connected.
