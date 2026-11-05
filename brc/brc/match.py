"""Three-way match: order quantity, goods issued, invoice price."""

from __future__ import annotations

from decimal import Decimal

from brc.model import (
    PRICE_TOL_EUR,
    QTY_TOL_KG,
    MatchLine,
    MatchResult,
    eur,
    kg,
)


def line_flags(
    ordered_kg: Decimal,
    shipped_kg: Decimal,
    billed_kg: Decimal,
    contract_eur_per_kg: Decimal,
    invoice_eur_per_kg: Decimal,
) -> list[str]:
    ordered_kg = kg(ordered_kg)
    shipped_kg = kg(shipped_kg)
    billed_kg = kg(billed_kg)
    contract_eur_per_kg = eur(contract_eur_per_kg)
    invoice_eur_per_kg = eur(invoice_eur_per_kg)
    flags: list[str] = []
    if abs(billed_kg - shipped_kg) > QTY_TOL_KG:
        flags.append("quantity_variance")
    elif ordered_kg - shipped_kg > QTY_TOL_KG:
        flags.append("short_delivery")
    if abs(invoice_eur_per_kg - contract_eur_per_kg) > PRICE_TOL_EUR:
        flags.append("price_variance")
    if not flags:
        flags.append("matched")
    return flags


def primary_flag(flags: list[str]) -> str:
    for name in ("price_variance", "quantity_variance", "short_delivery", "matched"):
        if name in flags:
            return name
    return flags[0]


def line_exposure(
    ordered_kg: Decimal,
    shipped_kg: Decimal,
    billed_kg: Decimal,
    contract_eur_per_kg: Decimal,
    invoice_eur_per_kg: Decimal,
    flags: list[str],
) -> Decimal:
    exposure = Decimal("0.00")
    if "price_variance" in flags:
        exposure += abs(eur(invoice_eur_per_kg) - eur(contract_eur_per_kg)) * kg(billed_kg)
    if "short_delivery" in flags:
        exposure += (kg(ordered_kg) - kg(shipped_kg)) * eur(contract_eur_per_kg)
    if "quantity_variance" in flags:
        exposure += abs(kg(billed_kg) - kg(shipped_kg)) * eur(invoice_eur_per_kg)
    return eur(exposure)


def match_line(
    sku: str,
    ordered_kg: Decimal,
    shipped_kg: Decimal,
    billed_kg: Decimal,
    contract_eur_per_kg: Decimal,
    invoice_eur_per_kg: Decimal,
) -> MatchLine:
    flags = line_flags(ordered_kg, shipped_kg, billed_kg, contract_eur_per_kg, invoice_eur_per_kg)
    return MatchLine(
        sku=sku,
        ordered_kg=kg(ordered_kg),
        shipped_kg=kg(shipped_kg),
        billed_kg=kg(billed_kg),
        contract_eur_per_kg=eur(contract_eur_per_kg),
        invoice_eur_per_kg=eur(invoice_eur_per_kg),
        flags=flags,
        exposure_eur=line_exposure(
            ordered_kg, shipped_kg, billed_kg, contract_eur_per_kg, invoice_eur_per_kg, flags
        ),
    )


def match_order(order_id: str, lines: list[MatchLine]) -> MatchResult:
    flags = [flag for line in lines for flag in line.flags if flag != "matched"]
    outside = bool(flags)
    status = primary_flag(flags or ["matched"])
    exposure = eur(sum((line.exposure_eur for line in lines), Decimal("0")))
    return MatchResult(
        order_id=order_id,
        lines=lines,
        status=status,
        outside_tolerance=outside,
        exposure_eur=exposure,
    )
