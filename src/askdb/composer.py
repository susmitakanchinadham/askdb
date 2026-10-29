"""Answer composer (SPEC 5.3): a short sentence built from result cells, plus display values.

The sentence is a template filled from the cells. A phraser (the model) may supply the wording
around the values as a template with `{c0}`, `{c1}` slots, but it never sees result values. Anything
that does not check out against the result falls back to the table alone with a neutral note.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from askdb import strings
from askdb.executor import ROW_CEILING, Page
from askdb.number_check import sentence_matches_result

# (interpretation, display column labels) -> template with {c0}, {c1}... slots, or None.
Phraser = Callable[[str | None, list[str]], str | None]

_ACRONYMS = {"mrr": "MRR", "arpu": "ARPU", "id": "ID", "usd": "USD", "sla": "SLA"}
_CURRENCY_HINTS = ("mrr", "revenue", "amount", "price", "arpu", "cost", "spend")
_PERCENT_HINTS = ("rate", "pct", "percent", "share", "ratio")
_SLOT = re.compile(r"\{c(\d+)\}")


@dataclass(frozen=True)
class Cell:
    text: str
    shown: Decimal | None  # the number as displayed (after any unit change)
    raw: Decimal | None  # the stored value


@dataclass(frozen=True)
class Composed:
    sentence: str | None
    note: str | None
    display_columns: list[str]
    display_rows: list[list[str]]
    truncation_note: str | None


def display_name(column: str) -> str:
    words = column.replace("-", "_").split("_")
    return " ".join(_ACRONYMS.get(w.lower(), w.capitalize()) for w in words if w)


def _kind(column: str) -> str:
    name = column.lower()
    if any(h in name for h in _PERCENT_HINTS):
        return "percent"
    if any(h in name for h in _CURRENCY_HINTS):
        return "currency"
    return "plain"


def format_cell(column: str, value: object) -> Cell:
    """Display text for one cell. NULL is "Not recorded", never zero."""
    if value is None:
        return Cell(strings.NOT_RECORDED, None, None)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return Cell(str(value), None, None)
    raw = Decimal(str(value))
    kind = _kind(column)
    if kind == "percent":
        shown = (raw * 100).quantize(Decimal("0.1"), ROUND_HALF_UP)
        return Cell(f"{shown:,}%", shown, raw)
    whole = raw == raw.to_integral_value()
    if kind == "currency":
        shown = raw.quantize(Decimal(1) if whole else Decimal("0.01"), ROUND_HALF_UP)
        return Cell(f"${shown:,}", shown, raw)
    if whole:
        return Cell(f"{int(raw):,}", raw, raw)
    shown = raw.quantize(Decimal("0.01"), ROUND_HALF_UP)
    return Cell(f"{shown:,}", shown, raw)


def _check(sentence: str, cells: Sequence[Cell], extras: Sequence[int], interp: str | None) -> bool:
    numbers = [n for c in cells for n in (c.shown, c.raw) if n is not None]
    return sentence_matches_result(
        sentence, [c.text for c in cells], numbers, extras, interpretation=interp
    )


def _default_sentence(interp: str | None, labels: list[str], cells: list[Cell], page: Page) -> str:
    if page.total == 0:
        lead = f"{strings.NO_RECORDS} {interp}." if interp else strings.NO_RECORDS
        return f"{lead} {strings.BROADEN}"
    if page.total == 1:
        pairs = [f"{label} {cell.text}" for label, cell in zip(labels, cells, strict=True)]
        if len(pairs) == 1:
            body = f"{labels[0]}: {cells[0].text}"
        else:
            body = "; ".join(pairs)
        return f"{interp}: {body}." if interp else f"{body}."
    count = f"{page.total:,}"
    if page.truncated:
        return strings.TRUNCATED.format(ceiling=f"{ROW_CEILING:,}")
    if interp:
        return strings.FOUND_FOR.format(total=count, interpretation=interp) + "."
    return strings.FOUND.format(total=count) + "."


def compose(interpretation: str | None, page: Page, phraser: Phraser | None = None) -> Composed:
    if interpretation is not None and not strings.is_clean(interpretation):
        interpretation = None
    labels = [display_name(c) for c in page.columns]
    grid = [
        [format_cell(col, v) for col, v in zip(page.columns, row, strict=True)] for row in page.rows
    ]
    display_rows = [[c.text for c in row] for row in grid]
    truncation = strings.TRUNCATED.format(ceiling=f"{ROW_CEILING:,}") if page.truncated else None

    single = grid[0] if page.total == 1 and len(grid) == 1 else None
    extras = [page.total, ROW_CEILING]
    cells = [c for row in grid[:1] for c in row] if single else []

    sentence = _default_sentence(interpretation, labels, single or [], page)
    if phraser is not None and single is not None:
        sentence = _phrased(phraser, interpretation, labels, single) or sentence

    trusted = _SLOT.search(sentence) is None and strings.is_clean(sentence)
    if not trusted or not _check(sentence, cells, extras, interpretation):
        return Composed(None, strings.TABLE_ONLY_NOTE, labels, display_rows, truncation)
    return Composed(sentence, None, labels, display_rows, truncation)


def _phrased(
    phraser: Phraser, interpretation: str | None, labels: list[str], cells: list[Cell]
) -> str | None:
    try:
        template = phraser(interpretation, labels)
    except Exception:  # a failing phraser never blocks the answer
        return None
    if not template:
        return None

    def fill(match: re.Match[str]) -> str:
        index = int(match.group(1))
        return cells[index].text if index < len(cells) else match.group(0)

    return _SLOT.sub(fill, template.strip())
