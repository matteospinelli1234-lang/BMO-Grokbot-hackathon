"""Canonical commercial documents. Money and kilos stay decimal until JSON."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any


BUSINESS_DATE = date(2026, 11, 5)
THRESHOLD_EUR = Decimal("500.00")
QTY_TOL_KG = Decimal("0.500")
PRICE_TOL_EUR = Decimal("0.01")
VAT_RATE = Decimal("4.00")


def D(value: Any) -> Decimal:
    return Decimal(str(value))


def kg(value: Any) -> Decimal:
    return D(value).quantize(Decimal("0.001"))


def eur(value: Any) -> Decimal:
    return D(value).quantize(Decimal("0.01"))


def dec_str(value: Decimal, places: str) -> str:
    return str(value.quantize(Decimal(places)))


@dataclass
class Party:
    name: str
    gln: str
    vat_country: str
    vat: str
    address: str
    cap: str
    city: str
    province: str

    def to_json(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Sku:
    code: str
    gtin: str
    name: str
    contract_eur_per_kg: Decimal
    list_eur_per_kg: Decimal
    on_hand_kg: Decimal
    lot: str
    qc: str

    def to_json(self) -> dict:
        return {
            "code": self.code,
            "gtin": self.gtin,
            "name": self.name,
            "contract_eur_per_kg": dec_str(self.contract_eur_per_kg, "0.01"),
            "list_eur_per_kg": dec_str(self.list_eur_per_kg, "0.01"),
            "on_hand_kg": dec_str(self.on_hand_kg, "0.001"),
            "lot": self.lot,
            "qc": self.qc,
        }


@dataclass
class Clause:
    id: str
    tag: str
    title: str
    text: str

    def to_json(self) -> dict:
        return self.__dict__.copy()


@dataclass
class OrderLine:
    sku: str
    gtin: str
    name: str
    qty_kg: Decimal
    price_eur_per_kg: Decimal

    def to_json(self) -> dict:
        return {
            "sku": self.sku,
            "gtin": self.gtin,
            "name": self.name,
            "qty_kg": dec_str(self.qty_kg, "0.001"),
            "price_eur_per_kg": dec_str(self.price_eur_per_kg, "0.01"),
        }


@dataclass
class Order:
    id: str
    lines: list[OrderLine]
    raw_edi: str

    def to_json(self) -> dict:
        return {"id": self.id, "lines": [ln.to_json() for ln in self.lines], "raw_edi": self.raw_edi}


@dataclass
class PlantEvent:
    id: str
    kind: str
    sku: str
    qty_kg: Decimal
    lot: str
    order_id: str | None
    detail: str

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "sku": self.sku,
            "qty_kg": dec_str(self.qty_kg, "0.001"),
            "lot": self.lot,
            "order_id": self.order_id,
            "detail": self.detail,
        }


@dataclass
class Shipment:
    id: str
    order_id: str
    lot: str
    sku: str
    qty_kg: Decimal
    raw_edi: str

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "order_id": self.order_id,
            "lot": self.lot,
            "sku": self.sku,
            "qty_kg": dec_str(self.qty_kg, "0.001"),
            "raw_edi": self.raw_edi,
        }


@dataclass
class Invoice:
    id: str
    order_id: str
    shipment_id: str
    sku: str
    qty_kg: Decimal
    price_eur_per_kg: Decimal
    imponibile: Decimal
    kind: str
    status: str
    raw_edi: str
    xml: str

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "order_id": self.order_id,
            "shipment_id": self.shipment_id,
            "sku": self.sku,
            "qty_kg": dec_str(self.qty_kg, "0.001"),
            "price_eur_per_kg": dec_str(self.price_eur_per_kg, "0.01"),
            "imponibile": dec_str(self.imponibile, "0.01"),
            "kind": self.kind,
            "status": self.status,
            "raw_edi": self.raw_edi,
            "xml": self.xml,
        }


@dataclass
class MatchLine:
    sku: str
    ordered_kg: Decimal
    shipped_kg: Decimal
    billed_kg: Decimal
    contract_eur_per_kg: Decimal
    invoice_eur_per_kg: Decimal
    flags: list[str]
    exposure_eur: Decimal

    def to_json(self) -> dict:
        return {
            "sku": self.sku,
            "ordered_kg": dec_str(self.ordered_kg, "0.001"),
            "shipped_kg": dec_str(self.shipped_kg, "0.001"),
            "billed_kg": dec_str(self.billed_kg, "0.001"),
            "contract_eur_per_kg": dec_str(self.contract_eur_per_kg, "0.01"),
            "invoice_eur_per_kg": dec_str(self.invoice_eur_per_kg, "0.01"),
            "flags": self.flags,
            "exposure_eur": dec_str(self.exposure_eur, "0.01"),
        }


@dataclass
class MatchResult:
    order_id: str
    lines: list[MatchLine]
    status: str
    outside_tolerance: bool
    exposure_eur: Decimal

    def to_json(self) -> dict:
        return {
            "order_id": self.order_id,
            "lines": [ln.to_json() for ln in self.lines],
            "status": self.status,
            "outside_tolerance": self.outside_tolerance,
            "exposure_eur": dec_str(self.exposure_eur, "0.01"),
        }


@dataclass
class Dispute:
    order_id: str
    kind: str
    sender: str
    subject: str
    body: str

    def to_json(self) -> dict:
        return {
            "order_id": self.order_id,
            "kind": self.kind,
            "from": self.sender,
            "subject": self.subject,
            "body": self.body,
        }


@dataclass
class Proposal:
    task: str
    order_id: str
    summary: str
    settlement: str
    amount_eur: Decimal
    evidence_ids: list[str]
    clause_ids: list[str]
    source: str

    def to_json(self) -> dict:
        return {
            "task": self.task,
            "order_id": self.order_id,
            "summary": self.summary,
            "settlement": self.settlement,
            "amount_eur": dec_str(self.amount_eur, "0.01"),
            "evidence_ids": list(self.evidence_ids),
            "clause_ids": list(self.clause_ids),
            "source": self.source,
        }


@dataclass
class Gate:
    human: bool
    reasons: list[str]
    amount_eur: Decimal

    def to_json(self) -> dict:
        return {
            "human": self.human,
            "reasons": self.reasons,
            "amount_eur": dec_str(self.amount_eur, "0.01"),
        }


@dataclass
class Decision:
    order_id: str
    status: str
    settlement: str
    actor: str | None
    amount_before: Decimal
    amount_after: Decimal
    evidence_ids: list[str]
    note: str

    def to_json(self) -> dict:
        return {
            "order_id": self.order_id,
            "status": self.status,
            "settlement": self.settlement,
            "actor": self.actor,
            "amount_before": dec_str(self.amount_before, "0.01"),
            "amount_after": dec_str(self.amount_after, "0.01"),
            "evidence_ids": self.evidence_ids,
            "note": self.note,
        }


@dataclass
class AuditEntry:
    at: str
    actor: str
    action: str
    order_id: str
    amount_before: Decimal
    amount_after: Decimal
    evidence_ids: list[str]
    note: str

    def to_json(self) -> dict:
        return {
            "at": self.at,
            "actor": self.actor,
            "action": self.action,
            "order_id": self.order_id,
            "amount_before": dec_str(self.amount_before, "0.01"),
            "amount_after": dec_str(self.amount_after, "0.01"),
            "evidence_ids": self.evidence_ids,
            "note": self.note,
        }


@dataclass
class Case:
    order_id: str
    kind: str
    title: str
    order: Order
    shipment: Shipment
    invoice: Invoice
    events: list[PlantEvent]
    match: MatchResult
    dispute: Dispute | None
    order_clerk: Proposal
    dispute_clerk: Proposal | None
    gate: Gate
    decision: Decision
    backorder_kg: Decimal
    credit_note: Invoice | None = None
    extra_events: list[PlantEvent] = field(default_factory=list)

    def document_ids(self, clause_ids: set[str]) -> set[str]:
        ids = {self.order.id, self.shipment.id, self.invoice.id, self.shipment.lot}
        ids.update(ev.id for ev in self.events)
        ids.update(clause_ids)
        if self.credit_note:
            ids.add(self.credit_note.id)
        return ids

    def to_json(self) -> dict:
        return {
            "order_id": self.order_id,
            "kind": self.kind,
            "title": self.title,
            "order": self.order.to_json(),
            "shipment": self.shipment.to_json(),
            "invoice": self.invoice.to_json(),
            "events": [ev.to_json() for ev in self.events],
            "match": self.match.to_json(),
            "dispute": self.dispute.to_json() if self.dispute else None,
            "order_clerk": self.order_clerk.to_json(),
            "dispute_clerk": self.dispute_clerk.to_json() if self.dispute_clerk else None,
            "gate": self.gate.to_json(),
            "decision": self.decision.to_json(),
            "backorder_kg": dec_str(self.backorder_kg, "0.001"),
            "credit_note": self.credit_note.to_json() if self.credit_note else None,
        }
