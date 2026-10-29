"""Sentence integrity (SPEC 5.3): every number and date in a sentence must come from the result.

Numbers may be formatted (thousands separators, currency, percent) or rounded. Text the model
wrote about the question itself (the interpretation) is removed before checking, so only the part
that claims values is judged. Names come only from cells through the template and are not checked.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation

_DATE = re.compile(r"\d{4}-\d{2}(?:-\d{2})?(?:[ T]\d{2}:\d{2}(?::\d{2})?)?")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _to_decimal(token: str) -> Decimal | None:
    try:
        return Decimal(token.replace(",", ""))
    except InvalidOperation:
        return None


def _decimals(token: str) -> int:
    return len(token.split(".", 1)[1]) if "." in token else 0


def matches_a_value(token: str, values: Iterable[Decimal]) -> bool:
    """True when `token` equals a value, or a value rounded to the token's own precision."""
    shown = _to_decimal(token)
    if shown is None:
        return False
    tolerance = Decimal(5) / (Decimal(10) ** (_decimals(token) + 1))
    return any(abs(shown - v) <= tolerance for v in values)


def sentence_matches_result(
    sentence: str,
    cell_texts: Iterable[str],
    cell_numbers: Iterable[Decimal],
    extra_numbers: Iterable[int] = (),
    interpretation: str | None = None,
) -> bool:
    """Check `sentence` against displayed cell texts and numeric cell values.

    `extra_numbers` are counts the composer states itself (rows found, the row ceiling).
    """
    text = sentence
    if interpretation:
        text = text.replace(interpretation, " ")
    texts = list(cell_texts)

    for found in _DATE.findall(text):
        if not any(found in t for t in texts):
            return False
    text = _DATE.sub(" ", text)

    allowed = [*cell_numbers, *(Decimal(n) for n in extra_numbers)]
    for token in _NUMBER.findall(text):
        if not matches_a_value(token, allowed):
            return False
    return True
