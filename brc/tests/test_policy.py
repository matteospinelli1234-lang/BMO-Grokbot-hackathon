from decimal import Decimal

from brc.match import match_line, match_order
from brc.model import eur, kg
from brc.policy import decide


def _match(ordered, shipped, billed, contract, invoice):
    line = match_line("CAR", kg(ordered), kg(shipped), kg(billed), eur(contract), eur(invoice))
    return match_order("ORD", [line])


def test_clean_under_threshold_posts_itself():
    gate = decide(_match("200", "200", "200", "1.85", "1.85"), eur("370.00"))
    assert gate.human is False
    assert gate.amount_eur == eur("370.00")


def test_large_clean_invoice_waits():
    gate = decide(_match("1000", "1000", "1000", "1.85", "1.85"), eur("1850.00"))
    assert gate.human is True
    assert any("soglia" in reason for reason in gate.reasons)


def test_quality_writeoff_waits_even_under_threshold():
    gate = decide(
        _match("300", "300", "300", "1.20", "1.20"),
        eur("360.00"),
        dispute_kind="quality",
        quality_amount=eur("360.00"),
    )
    assert gate.human is True
    assert gate.amount_eur == eur("360.00")
    assert any("qualità" in reason for reason in gate.reasons)


def test_price_delta_above_threshold_waits():
    gate = decide(_match("2200", "2200", "2200", "1.85", "2.40"), eur("5280.00"))
    assert gate.human is True
    assert gate.amount_eur == eur("1210.00")
    assert gate.amount_eur > Decimal("500")
