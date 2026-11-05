"""Order to stock, plant events, despatch, and draft invoice.

Only GOODS_ISSUED creates a despatch and an invoice. A mill batch is not a shipment.
"""

from __future__ import annotations

from decimal import Decimal

from brc.edi import doc_suffix, write_desadv, write_invoic
from brc.fattura import build_invoice
from brc.model import BUSINESS_DATE, Invoice, Order, OrderLine, PlantEvent, Shipment, Sku, eur, kg


def lot_history(sku: Sku, opening_kg: Decimal) -> list[PlantEvent]:
    if sku.qc == "pass":
        qc_detail = "Controllo chicco positivo."
    else:
        qc_detail = "Chicco rotto e gessato: controllo negativo."
    return [
        PlantEvent(
            id=f"{sku.lot}-MILLED",
            kind="MILLED",
            sku=sku.code,
            qty_kg=kg(opening_kg),
            lot=sku.lot,
            order_id=None,
            detail="Lotto già macinato, prima di questo ordine.",
        ),
        PlantEvent(
            id=f"{sku.lot}-QC",
            kind="QC_RESULT",
            sku=sku.code,
            qty_kg=kg(opening_kg),
            lot=sku.lot,
            order_id=None,
            detail=qc_detail,
        ),
        PlantEvent(
            id=f"{sku.lot}-PACKED",
            kind="PACKED",
            sku=sku.code,
            qty_kg=kg(opening_kg),
            lot=sku.lot,
            order_id=None,
            detail="Confezionato e a magazzino.",
        ),
    ]


def fulfill(
    supplier,
    buyer,
    sku: Sku,
    order: Order,
    opening_kg: Decimal,
    available_kg: Decimal,
    invoice_price_source: str,
    ship_despite_qc: bool,
) -> dict:
    line: OrderLine = order.lines[0]
    ordered = kg(line.qty_kg)
    available = kg(available_kg)
    ship_qty = min(ordered, available)
    if sku.qc == "fail" and not ship_despite_qc:
        ship_qty = kg(0)
    events = lot_history(sku, opening_kg)
    shortfall = kg(ordered - ship_qty)
    if shortfall > 0:
        events.append(
            PlantEvent(
                id=f"{order.id}-MILLED",
                kind="MILLED",
                sku=sku.code,
                qty_kg=shortfall,
                lot=f"LOT-{sku.code}-{order.id}",
                order_id=order.id,
                detail="Macinazione del saldo. Non è ancora uscita merce, quindi non si fattura.",
            )
        )
    events.append(
        PlantEvent(
            id=f"{order.id}-ISSUED",
            kind="GOODS_ISSUED",
            sku=sku.code,
            qty_kg=ship_qty,
            lot=sku.lot,
            order_id=order.id,
            detail="Uscita merce. Solo questo evento apre la bolla e la fattura.",
        )
    )
    des_id = doc_suffix(order.id, "DES")
    inv_id = doc_suffix(order.id, "INV")
    if invoice_price_source == "list":
        unit = eur(sku.list_eur_per_kg)
    else:
        unit = eur(sku.contract_eur_per_kg)
    imponibile = eur(ship_qty * unit)
    desadv = write_desadv(des_id, order.id, sku.gtin, ship_qty, sku.lot)
    invoic = write_invoic(inv_id, order.id, des_id, sku.gtin, ship_qty, unit, imponibile)
    xml = build_invoice(
        supplier,
        buyer,
        inv_id,
        order.id,
        des_id,
        sku.name,
        ship_qty,
        unit,
        BUSINESS_DATE,
        tipo="TD01",
    )
    shipment = Shipment(
        id=des_id,
        order_id=order.id,
        lot=sku.lot,
        sku=sku.code,
        qty_kg=ship_qty,
        raw_edi=desadv,
    )
    invoice = Invoice(
        id=inv_id,
        order_id=order.id,
        shipment_id=des_id,
        sku=sku.code,
        qty_kg=ship_qty,
        price_eur_per_kg=unit,
        imponibile=imponibile,
        kind="TD01",
        status="draft",
        raw_edi=invoic,
        xml=xml,
    )
    return {
        "events": events,
        "shipment": shipment,
        "invoice": invoice,
        "backorder_kg": shortfall,
        "ship_qty": ship_qty,
    }
