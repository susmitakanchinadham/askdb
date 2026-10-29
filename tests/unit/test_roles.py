from pathlib import Path

import pytest

from askdb.config import ConfigError, load_semantic_layer, render_model_text
from askdb.roles import (
    hidden_terms,
    layer_for_role,
    load_roles,
    redact_description,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def layer():
    return load_semantic_layer(ROOT / "config/databases/cloudmetrics.yaml")


@pytest.fixture(scope="module")
def book(layer):
    return load_roles(ROOT / "config/roles/cloudmetrics.yaml", layer)


def write(tmp_path, text):
    p = tmp_path / "roles.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_shipped_roles(book):
    assert set(book.roles) == {"admin", "analyst", "support"}
    assert "account_owner" in book.get("admin").allowed["customers"]
    assert "account_owner" not in book.get("analyst").allowed["customers"]
    assert set(book.get("support").allowed) == {"customers", "support_tickets"}
    assert book.get("nobody") is None


def test_admin_sees_exactly_the_full_layer(layer, book):
    full = layer_for_role(layer, book.get("admin"))
    assert render_model_text(full) == render_model_text(layer)
    assert hidden_terms(layer, book.get("admin")) == frozenset()


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("roles: {a: {tables: '*'}}\n", "is for None"),
        ("database: cloudmetrics\nroles: {}\n", "non-empty"),
        ("database: cloudmetrics\nroles: {a: {}}\n", "needs 'tables'"),
        ("database: cloudmetrics\nroles: {a: {tables: {nope: '*'}}}\n", "unknown table"),
        ("database: cloudmetrics\nroles: {a: {tables: {plans: [nope]}}}\n", "unknown column"),
        ("database: cloudmetrics\nroles: {a: {tables: {plans: []}}}\n", "must be"),
        ("database: other\nroles: {a: {tables: '*'}}\n", "not 'cloudmetrics'"),
    ],
)
def test_bad_roles_files_are_refused(tmp_path, layer, text, message):
    with pytest.raises(ConfigError, match=message):
        load_roles(write(tmp_path, text), layer)


def test_column_and_table_names_are_case_insensitive(tmp_path, layer):
    path = write(tmp_path, "database: cloudmetrics\nroles: {a: {tables: {Plans: [Plan_Name]}}}\n")
    assert load_roles(path, layer).get("a").allowed == {"plans": frozenset({"plan_name"})}


def test_analyst_loses_only_the_account_owner(layer, book):
    role = book.get("analyst")
    assert hidden_terms(layer, role) == {"account_owner"}
    narrowed = layer_for_role(layer, role)
    customers = next(t for t in narrowed.tables if t.name == "customers")
    assert [c.name for c in customers.columns] == [
        "customer_id",
        "company_name",
        "industry",
        "company_size",
        "country",
        "signup_date",
    ]
    assert len(narrowed.tables) == len(layer.tables)
    assert "account_owner" not in render_model_text(narrowed)


def test_support_loses_tables_references_and_the_text_about_them(layer, book):
    role = book.get("support")
    narrowed = layer_for_role(layer, role)
    assert [t.name for t in narrowed.tables] == ["customers", "support_tickets"]
    refs = [c.references for t in narrowed.tables for c in t.columns if c.references]
    assert refs == ["customers.customer_id"]
    text = render_model_text(narrowed).lower()
    for gone in ("subscription", "invoice", "mrr", "plans", "account_owner"):
        assert gone not in text, gone
    assert "support_tickets" in text and "resolution time" in text


def test_shared_column_names_are_not_treated_as_hidden(layer, book):
    # `status` exists on hidden subscriptions but stays visible on support_tickets.
    assert "status" not in hidden_terms(layer, book.get("support"))


def test_hidden_table_hides_its_singular_form(layer, book):
    terms = hidden_terms(layer, book.get("support"))
    assert {"invoices", "invoice", "subscriptions", "subscription"} <= terms


def test_redaction_keeps_unrelated_paragraphs_and_drops_whole_bullets():
    text = (
        "Intro about everything.\n\n"
        "Rules:\n"
        "- Keep this one.\n"
        "- Mentions secret_col here\n"
        "  and on a second line.\n"
        "- Also keep this.\n\n"
        "Only secrets:\n"
        "- secret_col again\n"
    )
    out = redact_description(text, frozenset({"secret_col"}))
    assert "secret_col" not in out and "second line" not in out
    assert "Keep this one." in out and "Also keep this." in out
    assert "Intro about everything." in out
    assert "Only secrets" not in out  # its list is gone, so the heading goes too


def test_redaction_without_terms_changes_nothing():
    assert redact_description("a\n\n- b\n", frozenset()) == "a\n\n- b\n"
