from datetime import UTC, datetime
from decimal import Decimal

import pytest

from askdb import strings
from askdb.composer import compose, display_name, format_cell
from askdb.executor import ROW_CEILING, Page
from askdb.number_check import sentence_matches_result

INTERP = "Active MRR, fiscal 2025"


def page(columns, rows, total=None, truncated=False):
    return Page(
        columns=columns,
        rows=[tuple(r) for r in rows],
        page=1,
        page_size=25,
        total=len(rows) if total is None else total,
        truncated=truncated,
        as_of=datetime.now(UTC),
    )


# ------------------------------------------------------------------ formatting


@pytest.mark.parametrize(
    ("column", "value", "text"),
    [
        ("total_mrr", 12345, "$12,345"),
        ("total_revenue", 1234.5, "$1,234.50"),
        ("churn_rate", 0.0425, "4.3%"),
        ("customer_count", 1200, "1,200"),
        ("avg_resolution_hours", 3.14159, "3.14"),
        ("month", "2025-03", "2025-03"),
        ("total_mrr", None, "Not recorded"),
        ("customer_count", None, "Not recorded"),
    ],
)
def test_format_cell(column, value, text):
    assert format_cell(column, value).text == text


def test_null_is_never_shown_as_zero():
    assert format_cell("total_revenue", None).text == strings.NOT_RECORDED


def test_display_names_are_business_wording():
    assert display_name("total_mrr") == "Total MRR"
    assert display_name("churned_subscriptions") == "Churned Subscriptions"
    assert display_name("plan_id") == "Plan ID"


# ------------------------------------------------------------------ sentences


def test_single_value_sentence_uses_the_cell():
    out = compose(INTERP, page(["total_mrr"], [(98765.5,)]))
    assert out.sentence == f"{INTERP}: Total MRR: $98,765.50."
    assert out.note is None
    assert out.display_rows == [["$98,765.50"]]


def test_single_row_with_several_values():
    out = compose(INTERP, page(["active_subscriptions", "total_mrr"], [(40, 1200)]))
    assert out.sentence == f"{INTERP}: Active Subscriptions 40; Total MRR $1,200."


def test_many_rows_state_the_count():
    out = compose(INTERP, page(["plan_name"], [("a",), ("b",), ("c",)]))
    assert out.sentence == f"Found 3 results for {INTERP}."


def test_over_ceiling_says_so_and_suggests_narrowing():
    out = compose(INTERP, page(["id"], [(1,)] * 25, total=ROW_CEILING, truncated=True))
    assert "more than 1,000 results" in out.sentence
    assert "narrowing" in out.sentence
    assert out.truncation_note == out.sentence


def test_empty_result_restates_the_interpretation_and_suggests_broadening():
    out = compose(INTERP, page(["total_mrr"], []))
    assert out.sentence.startswith("No matching records found.")
    assert INTERP in out.sentence
    assert "Try a wider period" in out.sentence
    assert out.display_rows == []


def test_null_aggregate_reads_not_recorded():
    out = compose(INTERP, page(["total_revenue"], [(None,)]))
    assert "Not recorded" in out.sentence
    assert "$0" not in out.sentence and " 0" not in out.sentence


def test_divide_by_zero_comes_back_as_null_and_reads_not_recorded():
    import sqlite3

    value = sqlite3.connect(":memory:").execute("SELECT 1.0 / 0").fetchone()[0]
    assert value is None
    out = compose(INTERP, page(["churn_rate"], [(value,)]))
    assert out.sentence == f"{INTERP}: Churn Rate: Not recorded."


def test_interpretation_with_banned_wording_is_dropped():
    out = compose("Rows in the sales table", page(["total_mrr"], [(5,)]))
    assert out.sentence == "Total MRR: $5."


# ------------------------------------------------------------------ phraser


def test_phraser_wording_is_filled_from_cells():
    out = compose(
        INTERP,
        page(["total_mrr"], [(5000,)]),
        phraser=lambda interp, labels: f"We are at {{c0}} right now ({labels[0]}).",
    )
    assert out.sentence == "We are at $5,000 right now (Total MRR)."
    assert out.note is None


def test_phraser_that_invents_a_number_falls_back_to_the_table_only():
    out = compose(
        INTERP, page(["total_mrr"], [(5000,)]), phraser=lambda i, labels: "Up 20% to $6,100"
    )
    assert out.sentence is None
    assert out.note == strings.TABLE_ONLY_NOTE
    assert out.display_rows == [["$5,000"]]


def test_phraser_that_gets_a_value_wrong_falls_back_to_the_table_only():
    out = compose(
        INTERP, page(["total_mrr"], [(5000,)]), phraser=lambda i, labels: "MRR is $5,001."
    )
    assert out.sentence is None and out.note == strings.TABLE_ONLY_NOTE


def test_phraser_with_an_unknown_slot_falls_back_to_the_table_only():
    out = compose(INTERP, page(["total_mrr"], [(5000,)]), phraser=lambda i, labels: "It is {c3}.")
    assert out.sentence is None and out.note == strings.TABLE_ONLY_NOTE


def test_phraser_using_banned_wording_falls_back_to_the_table_only():
    out = compose(
        INTERP, page(["total_mrr"], [(5000,)]), phraser=lambda i, labels: "This query gives {c0}."
    )
    assert out.sentence is None and out.note == strings.TABLE_ONLY_NOTE


def test_a_failing_or_silent_phraser_keeps_the_default_sentence():
    def boom(i, labels):
        raise RuntimeError

    for phraser in (boom, lambda i, labels: None):
        out = compose(INTERP, page(["total_mrr"], [(5000,)]), phraser=phraser)
        assert out.sentence == f"{INTERP}: Total MRR: $5,000."


def test_phraser_is_not_used_for_multi_row_results():
    out = compose(INTERP, page(["n"], [(1,), (2,)]), phraser=lambda i, labels: "Ignored {c0}")
    assert out.sentence == f"Found 2 results for {INTERP}."


# ------------------------------------------------------------------ number check


def check(sentence, texts=(), numbers=(), extras=(), interp=None):
    return sentence_matches_result(
        sentence, texts, [Decimal(str(n)) for n in numbers], extras, interpretation=interp
    )


def test_formatted_and_rounded_numbers_match():
    assert check("Revenue was $1,234.57.", numbers=[1234.5678])
    assert check("About 1,235 sales.", numbers=[1234.5678])
    assert not check("Revenue was $1,240.", numbers=[1234.5678])


def test_dates_must_appear_in_a_cell():
    assert check("Peak month was 2025-03.", texts=["2025-03"])
    assert not check("Peak month was 2025-04.", texts=["2025-03"])


def test_numbers_in_the_interpretation_are_not_judged():
    assert check("Fiscal 2025 total: 40.", numbers=[40], interp="Fiscal 2025")


def test_stated_counts_are_allowed():
    assert check("Found 7 results.", extras=[7])
    assert not check("Found 8 results.", extras=[7])
