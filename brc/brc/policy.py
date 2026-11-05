"""Post by itself only inside tolerance and under the euro threshold."""

from __future__ import annotations

from decimal import Decimal

from brc.model import THRESHOLD_EUR, Gate, MatchResult, eur


def decide(
    match: MatchResult,
    invoice_total: Decimal,
    dispute_kind: str | None = None,
    quality_amount: Decimal = Decimal("0"),
) -> Gate:
    invoice_total = eur(invoice_total)
    quality_amount = eur(quality_amount)
    reasons: list[str] = []
    flags = {flag for line in match.lines for flag in line.flags}

    if "short_delivery" in flags:
        reasons.append("Consegna inferiore all'ordine.")
    if "quantity_variance" in flags:
        reasons.append("Quantità fatturata diversa dalla bolla.")
    if "price_variance" in flags:
        reasons.append("Prezzo fattura diverso dal contratto.")
    if invoice_total > THRESHOLD_EUR:
        reasons.append("Imponibile sopra la soglia di 500 euro.")
    if match.exposure_eur > THRESHOLD_EUR:
        reasons.append("Scostamento sopra la soglia di 500 euro.")
    if dispute_kind == "quality":
        reasons.append("Contestazione qualità: la nota di credito non si registra da sola.")

    human = bool(reasons)
    if not human:
        reasons.append("Dentro tolleranza e sotto la soglia di 500 euro.")

    if dispute_kind == "quality":
        amount = quality_amount
    elif match.exposure_eur > 0:
        amount = match.exposure_eur
    else:
        amount = invoice_total
    return Gate(human=human, reasons=reasons, amount_eur=eur(amount))
