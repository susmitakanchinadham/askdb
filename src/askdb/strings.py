"""Every fixed user-facing string, and the wording rules they must obey (SPEC 8).

Anything a person reads, including API errors, comes from here or is checked with `is_clean`.
`tests/unit/test_copy_lint.py` fails when any string below uses a banned word.
"""

from __future__ import annotations

import re

# Whole words only; plurals and the hyphenated spelling are included.
BANNED = re.compile(
    r"\b(sql|quer(?:y|ies)|tables?|columns?|time-?outs?|schema|stack ?trace)\b", re.IGNORECASE
)

MESSAGES = {
    "unavailable": "I can't answer right now, please try again shortly.",
    "refused": "I can't answer that reliably yet.",
    "failed": "I couldn't find an answer to that. Try rephrasing or narrowing the question.",
    "slow": "That search took too long. Try a shorter period or a narrower question.",
    "busy": "Lots of people are asking questions right now. Please try again shortly.",
    "not_permitted": "That information isn't available to your role.",
    "no_access": "You don't have access yet. Please sign in or contact your admin.",
    "invalid": "Please type a question.",
    "unexpected": "Something went wrong on our side. Please try again shortly.",
}

NOT_RECORDED = "Not recorded"
NO_RECORDS = "No matching records found."
BROADEN = "Try a wider period or fewer filters."
TABLE_ONLY_NOTE = "Here are the figures."
TRUNCATED = "There are more than {ceiling} results; try narrowing by period or by a category."
FOUND = "Found {total} results"
FOUND_FOR = "Found {total} results for {interpretation}"

ALL_STRINGS: tuple[str, ...] = (
    *MESSAGES.values(),
    NOT_RECORDED,
    NO_RECORDS,
    BROADEN,
    TABLE_ONLY_NOTE,
    TRUNCATED,
    FOUND,
    FOUND_FOR,
)


def find_banned(text: str) -> list[str]:
    return [m.group(0) for m in BANNED.finditer(text)]


def is_clean(text: str) -> bool:
    return not BANNED.search(text)
