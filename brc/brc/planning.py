"""Heuristic mill sketch from synthetic weekly orders. Not a production plan."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from brc.model import eur


SAFETY = Decimal("1.10")
WINDOW = 4


def suggest_batches(history: list[dict], names: dict[str, str]) -> list[dict]:
    weeks = sorted({row["week"] for row in history})
    window = set(weeks[-WINDOW:])
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    counts: dict[str, int] = defaultdict(int)
    for row in history:
        if row["week"] not in window:
            continue
        totals[row["sku"]] += Decimal(row["kg"])
        counts[row["sku"]] += 1
    rows = []
    for sku in sorted(totals):
        average = totals[sku] / Decimal(counts[sku])
        suggested = eur(average * SAFETY)
        rows.append(
            {
                "sku": sku,
                "name": names.get(sku, sku),
                "weeks": WINDOW,
                "average_kg": f"{average.quantize(Decimal('0.001'))}",
                "suggested_kg": f"{suggested.quantize(Decimal('0.001'))}",
                "basis": "media delle ultime 4 settimane x 1,10",
            }
        )
    return rows
