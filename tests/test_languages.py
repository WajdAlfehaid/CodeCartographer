"""Each adapter must find the right structures in real-shaped source."""

import os

import pytest

from codecartographer.languages import BY_NAME, detect, language_table

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def parse(name):
    path = os.path.join(FIXTURES, name)
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    adapter = detect(path, source)
    assert adapter is not None, f"no adapter matched {name}"
    return adapter, adapter.parse(path, name, source)


def names(sf):
    return [f.name for f in sf.functions]


# -- detection --------------------------------------------------------------

def test_every_registered_language_is_reachable_by_extension():
    for entry in language_table():
        assert entry["extensions"], f"{entry['name']} claims no extensions"


@pytest.mark.parametrize("filename,expected", [
    ("payroll.cbl", "cobol"),
    ("legacy.f", "fortran"),
    ("Billing.bas", "vb"),
    ("orders.sql", "sql"),
    ("Ledger.pas", "pascal"),
    ("pricing.py", "python"),
])
def test_detection(filename, expected):
    adapter, _ = parse(filename)
    assert adapter.name == expected


def test_ambiguous_extension_resolved_by_content():
    """`.f` could be Fortran; `.bas` could be several Basics. Content decides."""
    adapter = detect("x.cbl", "       IDENTIFICATION DIVISION.\n       PROGRAM-ID. X.")
    assert adapter.name == "cobol"


# -- Java -------------------------------------------------------------------

JAVA = """
package com.example.billing;

import java.util.List;

/** Handles invoices. */
public class Invoicer {
    private static final double TAX = 0.2;
    private Ledger ledger;

    public Invoicer(Ledger ledger) { this.ledger = ledger; }

    @Override
    public String toString() { return "Invoicer"; }

    private double applyTax(double amount) {
        if (amount > 100 && amount < 10000) {
            return amount * (1 + TAX);
        } else if (amount <= 0) {
            return 0;
        }
        return amount;
    }

    public void post(double amount) {
        ledger.record(applyTax(amount));
    }
}
"""


def test_java_structure():
    adapter = BY_NAME["java"]
    sf = adapter.parse("Invoicer.java", "Invoicer.java", JAVA)
    assert [t.name for t in sf.types] == ["Invoicer"]
    assert sf.package == "com.example.billing"
    assert set(names(sf)) == {"Invoicer", "toString", "applyTax", "post"}


def test_java_modifiers_do_not_bleed_between_declarations():
    """A private method following a public one must not inherit `public`."""
    sf = BY_NAME["java"].parse("Invoicer.java", "Invoicer.java", JAVA)
    apply_tax = next(f for f in sf.functions if f.name == "applyTax")
    post = next(f for f in sf.functions if f.name == "post")
    assert "private" in apply_tax.modifiers
    assert "public" not in apply_tax.modifiers
    assert "public" in post.modifiers
    assert "private" not in post.modifiers


def test_java_control_keywords_are_not_methods():
    sf = BY_NAME["java"].parse("Invoicer.java", "Invoicer.java", JAVA)
    assert "if" not in names(sf)
    assert "for" not in names(sf)
    assert "catch" not in names(sf)


def test_java_annotation_marks_an_entry_point():
    sf = BY_NAME["java"].parse("Invoicer.java", "Invoicer.java", JAVA)
    to_string = next(f for f in sf.functions if f.name == "toString")
    assert to_string.is_entry_point


def test_java_complexity_counts_boolean_operators():
    sf = BY_NAME["java"].parse("Invoicer.java", "Invoicer.java", JAVA)
    apply_tax = next(f for f in sf.functions if f.name == "applyTax")
    assert apply_tax.metrics["cyclomatic"] >= 4


def test_java_local_variable_types_are_recorded():
    src = """
public class A {
    public void go() {
        Order o = new Order();
        o.submit();
    }
}
"""
    sf = BY_NAME["java"].parse("A.java", "A.java", src)
    go = sf.functions[0]
    assert go.locals.get("o") == "Order"
    assert any(c.name == "submit" and c.receiver == "o" for c in go.calls)


# -- COBOL ------------------------------------------------------------------

def test_cobol_finds_paragraphs_not_statements():
    _, sf = parse("payroll.cbl")
    found = names(sf)
    assert "MAIN-PARAGRAPH" in found
    assert "CALCULATE-GROSS" in found
    assert "OLD-BONUS-ROUTINE" in found
    # Scope terminators and verbs are not paragraphs.
    assert "END-IF" not in found
    assert "MOVE" not in found
    assert "DISPLAY" not in found


def test_cobol_perform_is_a_call():
    _, sf = parse("payroll.cbl")
    main = next(f for f in sf.functions if f.name == "MAIN-PARAGRAPH")
    called = {c.name for c in main.calls}
    assert {"INITIALIZE-RUN", "CALCULATE-GROSS", "WRITE-PAYSLIP"} <= called


def test_cobol_working_storage_becomes_fields():
    _, sf = parse("payroll.cbl")
    globals_by_name = {g.name: g for g in sf.globals}
    assert "WS-TAX-RATE" in globals_by_name
    assert globals_by_name["WS-TAX-RATE"].initial_value is not None


# -- Fortran ----------------------------------------------------------------

def test_fortran_subroutines_do_not_swallow_each_other():
    """A labelled CONTINUE closes a fixed-form DO loop, not the subroutine."""
    _, sf = parse("legacy.f")
    assert set(names(sf)) == {"HEATSIM", "INITGRID", "SOLVE", "DEADROUTINE"}
    solve = next(f for f in sf.functions if f.name == "SOLVE")
    assert solve.end_line - solve.line < 15


def test_fortran_call_statement():
    _, sf = parse("legacy.f")
    main = next(f for f in sf.functions if f.name == "HEATSIM")
    assert {"INITGRID", "SOLVE"} <= {c.name for c in main.calls}


# -- Visual Basic -----------------------------------------------------------

def test_vb_finds_both_subs_and_functions():
    _, sf = parse("Billing.bas")
    assert set(names(sf)) == {
        "CalculateInvoiceTotal", "GetTaxRate", "PrintInvoice", "ObsoleteHelper"}


def test_vb_bare_function_call_is_detected():
    _, sf = parse("Billing.bas")
    total = next(f for f in sf.functions if f.name == "CalculateInvoiceTotal")
    assert "GetTaxRate" in {c.name for c in total.calls}


def test_vb_constant_is_a_global():
    _, sf = parse("Billing.bas")
    assert "DISCOUNT_THRESHOLD" in {g.name for g in sf.globals}


# -- Pascal -----------------------------------------------------------------

def test_pascal_reports_implementations_not_forward_declarations():
    _, sf = parse("Ledger.pas")
    assert names(sf).count("ApplyInterest") == 1
    assert names(sf).count("PostEntry") == 1


def test_pascal_qualified_name_sets_the_owner():
    _, sf = parse("Ledger.pas")
    apply_interest = next(f for f in sf.functions if f.name == "ApplyInterest")
    assert apply_interest.owner == "TLedger"


# -- SQL --------------------------------------------------------------------

def test_sql_procedures_and_functions():
    _, sf = parse("orders.sql")
    assert set(names(sf)) == {"calculate_order_total", "customer_tier"}


def test_sql_procedure_body_is_bounded():
    _, sf = parse("orders.sql")
    proc = next(f for f in sf.functions if f.name == "calculate_order_total")
    assert proc.end_line < 24     # must not run into the following CREATE


# -- Python -----------------------------------------------------------------

def test_python_uses_the_real_ast():
    _, sf = parse("pricing.py")
    assert [t.name for t in sf.types] == ["PricingEngine"]
    assert "_volume_discount" in names(sf)
    engine = next(f for f in sf.functions if f.name == "total_price")
    assert engine.params[0].name == "subtotal"      # `self` is dropped


def test_python_visibility_follows_the_underscore_convention():
    _, sf = parse("pricing.py")
    private = next(f for f in sf.functions if f.name == "_volume_discount")
    public = next(f for f in sf.functions if f.name == "shipping_cost")
    assert not private.is_public
    assert public.is_public


def test_python_syntax_error_is_reported_with_a_line():
    sf = BY_NAME["python"].parse("bad.py", "bad.py", "def f(:\n    pass\n")
    assert any(d.code == "syntax-error" for d in sf.diagnostics)
    assert sf.parse_confidence < 1.0


def test_python_nested_helpers_are_not_top_level_functions():
    src = "def outer():\n    def inner():\n        return 1\n    return inner()\n"
    sf = BY_NAME["python"].parse("n.py", "n.py", src)
    assert names(sf) == ["outer"]
