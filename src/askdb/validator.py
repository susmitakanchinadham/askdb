"""SQL validator (SPEC 5.1): accept exactly one SELECT, judged on the parsed tree.

`validate_sql` never raises on bad input. It returns a result with a machine-readable code and a
message meant for the planner's retry (never shown to end users). On success `result.sql` is
regenerated from the validated tree with every column qualified and `*` expanded, so the text that
runs is the text that was checked, and a restricted allowlist cannot be bypassed with `SELECT *`.

Anything the validator does not recognise is rejected. The authorizer (slice 3) is a second layer.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

from askdb.config import SemanticLayer

DIALECT = "sqlite"

Allowed = Mapping[str, Collection[str]]  # table -> columns the caller may read


class Code(StrEnum):
    EMPTY = "empty"
    PARSE_ERROR = "parse_error"
    MULTIPLE_STATEMENTS = "multiple_statements"
    NOT_SELECT = "not_select"
    PLACEHOLDER = "placeholder"
    RECURSIVE_CTE = "recursive_cte"
    TABLE_FUNCTION = "table_function"
    QUALIFIED_TABLE = "qualified_table"
    TABLE_NOT_ALLOWED = "table_not_allowed"
    FUNCTION_NOT_ALLOWED = "function_not_allowed"
    UNKNOWN_COLUMN = "unknown_column"
    ANALYSIS_ERROR = "analysis_error"


@dataclass(frozen=True)
class ValidationResult:
    code: Code | None = None
    message: str = ""
    sql: str = ""  # regenerated from the validated tree; run this, never the original text
    tables: frozenset[str] = field(default_factory=frozenset)
    columns: frozenset[tuple[str, str]] = field(default_factory=frozenset)

    @property
    def ok(self) -> bool:
        return self.code is None


def allowed_from_layer(layer: SemanticLayer) -> dict[str, frozenset[str]]:
    """Every table and column in the semantic layer (no role filtering yet, see slice 6)."""
    return {t.name.lower(): frozenset(c.name.lower() for c in t.columns) for t in layer.tables}


def _node_types(*names: str) -> tuple[type[exp.Expression], ...]:
    return tuple(getattr(exp, n) for n in names if hasattr(exp, n))


# Statements and clauses that must never appear anywhere in the tree, even under a SELECT.
_FORBIDDEN_NODES = _node_types(
    "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable",
    "TruncateTable", "Command", "Pragma", "Attach", "Detach", "Transaction", "Commit",
    "Rollback", "Set", "Use", "Copy", "Analyze", "Into", "Lateral", "Unnest",
)  # fmt: skip
_PLACEHOLDERS = _node_types("Placeholder", "Parameter", "SessionParameter")

# Functions sqlglot has no class for come through as `Anonymous`; only these may pass.
_SAFE_ANONYMOUS = frozenset({"datetime", "julianday", "time", "total", "unixepoch"})

# Functions and operators sqlglot types. Anything else (random, hex, char, json_extract, version
# and so on) is refused until a question needs it.
_SAFE_TYPED = frozenset(
    {
        "And", "Or", "Case", "Cast", "If", "Exists",
        "Count", "Sum", "Avg", "Min", "Max", "Greatest", "Least", "GroupConcat",
        "Coalesce", "Nullif", "Abs", "Round", "Ceil", "Floor", "Sign",
        "Length", "Lower", "Upper", "Trim", "Replace", "Substring", "StrPosition",
        "Date", "TimeToStr", "TsOrDsToTimestamp", "TsOrDsToDate",
        "RowNumber", "Rank", "DenseRank",
    }
)  # fmt: skip


def _fail(code: Code, message: str) -> ValidationResult:
    return ValidationResult(code=code, message=message)


def validate_sql(sql: str, allowed: Allowed) -> ValidationResult:
    if not sql or not sql.strip():
        return _fail(Code.EMPTY, "No statement was given.")

    try:
        parsed = sqlglot.parse(sql, read=DIALECT)
    except (SqlglotError, RecursionError) as exc:
        return _fail(Code.PARSE_ERROR, f"The statement could not be parsed: {_first_line(exc)}")

    statements = [s for s in parsed if s is not None]
    if not statements:
        return _fail(Code.EMPTY, "No statement was given.")
    if len(statements) > 1:
        return _fail(Code.MULTIPLE_STATEMENTS, "Exactly one SELECT statement is allowed.")

    root = statements[0]
    if not isinstance(root, exp.Query) or isinstance(root, exp.Subquery):
        return _fail(Code.NOT_SELECT, "Only a SELECT statement is allowed.")

    try:
        return _validate_tree(
            root, {t.lower(): {c.lower() for c in cs} for t, cs in allowed.items()}
        )
    except RecursionError:
        return _fail(Code.ANALYSIS_ERROR, "The statement is nested too deeply.")
    except Exception as exc:  # fail closed on anything the checks did not anticipate
        return _fail(Code.ANALYSIS_ERROR, f"The statement could not be checked: {_first_line(exc)}")


def _validate_tree(root: exp.Expression, allowed: dict[str, set[str]]) -> ValidationResult:
    for node in root.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            return _fail(Code.NOT_SELECT, f"{node.key.upper()} is not allowed; only SELECT is.")
        if isinstance(node, _PLACEHOLDERS):
            return _fail(Code.PLACEHOLDER, "Parameters and placeholders are not allowed.")
        if isinstance(node, exp.Select) and not node.expressions:
            return _fail(Code.PARSE_ERROR, "The SELECT has nothing to select.")
        if isinstance(node, exp.With) and node.args.get("recursive"):
            return _fail(Code.RECURSIVE_CTE, "Recursive queries are not allowed.")

    tables = _check_tables(root, allowed)
    if isinstance(tables, ValidationResult):
        return tables

    bad = _check_functions(root)
    if bad is not None:
        return bad

    schema = {t: dict.fromkeys(sorted(cols), "text") for t, cols in allowed.items()}
    try:
        qualified = qualify(
            root.copy(), schema=schema, dialect=DIALECT, validate_qualify_columns=True
        )
    except OptimizeError as exc:
        return _fail(Code.UNKNOWN_COLUMN, f"Unknown or unavailable column: {_first_line(exc)}")

    for node in qualified.find_all(exp.Expression):
        node.comments = None  # comment text never reaches the statement that runs

    bad = _check_resolved(qualified)
    if bad is not None:
        return bad

    columns = set()
    for scope in traverse_scope(qualified):
        for col in scope.columns:
            source = scope.sources.get(col.table)
            if isinstance(source, exp.Table):
                columns.add((source.name.lower(), col.name.lower()))

    return ValidationResult(
        sql=qualified.sql(dialect=DIALECT),
        tables=frozenset(tables),
        columns=frozenset(columns),
    )


def _check_tables(root: exp.Expression, allowed: dict[str, set[str]]):
    """Return the set of real tables read, or a failure. CTE names are never trusted as tables."""
    for table in root.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            return _fail(Code.TABLE_FUNCTION, "Table-valued functions are not allowed.")

    cte_names = {cte.alias.lower() for cte in root.find_all(exp.CTE)}
    real: list[exp.Table] = []
    for scope in traverse_scope(root):
        for table in scope.tables:
            if isinstance(scope.sources.get(table.alias_or_name), exp.Table):
                real.append(table)

    real_ids = {id(t) for t in real}
    for table in root.find_all(exp.Table):
        if id(table) not in real_ids and table.name.lower() not in cte_names:
            return _fail(Code.TABLE_NOT_ALLOWED, f"Table '{table.name}' is not available.")

    names = set()
    for table in real:
        if table.args.get("db") or table.args.get("catalog"):
            return _fail(Code.QUALIFIED_TABLE, "Schema-qualified table names are not allowed.")
        name = table.name.lower()
        if name not in allowed:
            return _fail(Code.TABLE_NOT_ALLOWED, f"Table '{table.name}' is not available.")
        names.add(name)
    return names


def _check_resolved(qualified: exp.Expression) -> ValidationResult | None:
    """Every column in the rewritten tree must be pinned to a source table.

    `qualify` does not validate a bare name in HAVING (it may be an alias), and SQLite resolves
    such a name to a real column before an alias, so an unresolved name could read a column the
    caller may not see. The one exception is a bare ORDER BY term naming a result column, which
    SQLite always reads as that result column.
    """
    for star in qualified.find_all(exp.Star):
        if not isinstance(star.parent, exp.Count):
            return _fail(Code.UNKNOWN_COLUMN, "Name the columns you need instead of using *.")

    for col in qualified.find_all(exp.Column):
        if not col.table and not _is_result_column_in_order_by(col):
            return _fail(Code.UNKNOWN_COLUMN, f"Column '{col.name}' could not be resolved.")
    return None


def _is_result_column_in_order_by(col: exp.Column) -> bool:
    ordered = col.parent
    if not isinstance(ordered, exp.Ordered) or not isinstance(ordered.parent, exp.Order):
        return False
    owner = ordered.parent.parent  # a Window here means OVER (ORDER BY ...), which is not one
    return isinstance(owner, exp.Query) and col.name.lower() in {
        name.lower() for name in owner.named_selects
    }


def _check_functions(root: exp.Expression) -> ValidationResult | None:
    for func in root.find_all(exp.Func):
        if isinstance(func, exp.Anonymous):
            name = func.name.lower()
            if name not in _SAFE_ANONYMOUS:
                return _fail(Code.FUNCTION_NOT_ALLOWED, f"Function '{func.name}' is not allowed.")
        elif type(func).__name__ not in _SAFE_TYPED:
            return _fail(
                Code.FUNCTION_NOT_ALLOWED, f"Function {func.sql(DIALECT)[:40]} is not allowed."
            )
    return None


def _first_line(exc: Exception) -> str:
    return (str(exc).splitlines() or [""])[0][:200]
