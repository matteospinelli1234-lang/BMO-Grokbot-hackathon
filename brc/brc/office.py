"""In-memory company: four dossiers, a gate, and an audit log."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from brc.agents import load_fixture, propose
from brc.edi import doc_suffix, parse_orders, write_invoic
from brc.fattura import build_invoice
from brc.match import match_line, match_order
from brc.policy import decide
from brc.model import (
    BUSINESS_DATE,
    THRESHOLD_EUR,
    AuditEntry,
    Case,
    Clause,
    Decision,
    Dispute,
    Invoice,
    Order,
    OrderLine,
    Party,
    Sku,
    dec_str,
    eur,
    kg,
)
from brc.pipeline import fulfill
from brc.planning import suggest_batches

DATA = Path(__file__).resolve().parent.parent / "data"
ROME = timezone(timedelta(hours=1))


def _load(name: str):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def _party(payload: dict) -> Party:
    return Party(**payload)


def _sku(payload: dict) -> Sku:
    return Sku(
        code=payload["code"],
        gtin=payload["gtin"],
        name=payload["name"],
        contract_eur_per_kg=eur(payload["contract_eur_per_kg"]),
        list_eur_per_kg=eur(payload["list_eur_per_kg"]),
        on_hand_kg=kg(payload["on_hand_kg"]),
        lot=payload["lot"],
        qc=payload["qc"],
    )


class Office:
    def __init__(self) -> None:
        catalog = _load("catalog.json")
        self.supplier = _party(catalog["supplier"])
        self.buyer = _party(catalog["buyer"])
        self.contract_id = catalog["contract_id"]
        self.skus = {row["code"]: _sku(row) for row in catalog["skus"]}
        self.by_gtin = {sku.gtin: sku for sku in self.skus.values()}
        self.clauses = [Clause(**row) for row in _load("clauses.json")]
        self.history = _load("history.json")
        self.cases: dict[str, Case] = {}
        self.audit: list[AuditEntry] = []
        stock = {code: sku.on_hand_kg for code, sku in self.skus.items()}
        opening = dict(stock)
        for scenario in _load("scenarios.json"):
            self.cases[scenario["order_id"]] = self._build(scenario, stock, opening)
        self._planning = suggest_batches(self.history, {code: sku.name for code, sku in self.skus.items()})

    def _read_order(self, order_id: str) -> Order:
        raw = (DATA / "edi" / f"ORDERS_{order_id}.edi").read_text(encoding="utf-8")
        parsed = parse_orders(raw)
        if parsed["id"] != order_id:
            raise ValueError(f"{order_id} file declares {parsed['id']}")
        lines = []
        for row in parsed["lines"]:
            sku = self.by_gtin[row["gtin"]]
            lines.append(
                OrderLine(
                    sku=sku.code,
                    gtin=sku.gtin,
                    name=sku.name,
                    qty_kg=kg(row["qty_kg"]),
                    price_eur_per_kg=eur(row["price"]),
                )
            )
        return Order(id=order_id, lines=lines, raw_edi=raw)

    def _dispute(self, order_id: str) -> Dispute | None:
        path = DATA / "emails" / f"{order_id}.json"
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return Dispute(
            order_id=order_id,
            kind=payload["kind"],
            sender=payload["from"],
            subject=payload["subject"],
            body=payload["body"],
        )

    def _build(self, scenario: dict, stock: dict[str, Decimal], opening: dict[str, Decimal]) -> Case:
        order = self._read_order(scenario["order_id"])
        sku = self.skus[order.lines[0].sku]
        done = fulfill(
            self.supplier,
            self.buyer,
            sku,
            order,
            opening[sku.code],
            stock[sku.code],
            scenario["invoice_price"],
            scenario["ship_despite_qc"],
        )
        stock[sku.code] = kg(stock[sku.code] - done["ship_qty"])
        line = match_line(
            sku.code,
            order.lines[0].qty_kg,
            done["shipment"].qty_kg,
            done["invoice"].qty_kg,
            sku.contract_eur_per_kg,
            done["invoice"].price_eur_per_kg,
        )
        matched = match_order(order.id, [line])
        dispute = self._dispute(order.id)
        quality_amount = done["invoice"].imponibile if dispute and dispute.kind == "quality" else Decimal("0")
        gate = decide(
            matched,
            done["invoice"].imponibile,
            dispute.kind if dispute else None,
            quality_amount,
        )
        clerk = load_fixture(order.id, "order")
        settler = load_fixture(order.id, "dispute") if dispute else None
        if gate.human:
            decision = Decision(
                order_id=order.id,
                status="pending",
                settlement=(settler or clerk).settlement,
                actor=None,
                amount_before=gate.amount_eur,
                amount_after=gate.amount_eur,
                evidence_ids=list((settler or clerk).evidence_ids),
                note="In attesa di una persona.",
            )
            done["invoice"].status = "draft"
        else:
            done["invoice"].status = "posted"
            decision = Decision(
                order_id=order.id,
                status="posted",
                settlement="post",
                actor="sistema",
                amount_before=done["invoice"].imponibile,
                amount_after=done["invoice"].imponibile,
                evidence_ids=list(clerk.evidence_ids),
                note=gate.reasons[0],
            )
            self.audit.append(
                AuditEntry(
                    at="2026-11-05T09:30:00+01:00",
                    actor="sistema",
                    action="auto_post",
                    order_id=order.id,
                    amount_before=done["invoice"].imponibile,
                    amount_after=done["invoice"].imponibile,
                    evidence_ids=list(clerk.evidence_ids),
                    note=gate.reasons[0],
                )
            )
        case = Case(
            order_id=order.id,
            kind=scenario["kind"],
            title=scenario["title"],
            order=order,
            shipment=done["shipment"],
            invoice=done["invoice"],
            events=done["events"],
            match=matched,
            dispute=dispute,
            order_clerk=clerk,
            dispute_clerk=settler,
            gate=gate,
            decision=decision,
            backorder_kg=done["backorder_kg"],
        )
        case.order_clerk = propose(case, self.clauses, "order")
        if dispute:
            case.dispute_clerk = propose(case, self.clauses, "dispute")
            case.decision.settlement = case.dispute_clerk.settlement
            case.decision.evidence_ids = list(case.dispute_clerk.evidence_ids)
        return case

    def _stamp(self) -> str:
        return datetime.now(ROME).isoformat(timespec="seconds")

    def _require_pending(self, order_id: str) -> Case:
        case = self.cases[order_id]
        if case.decision.status != "pending":
            raise ValueError(f"{order_id} non è in attesa ({case.decision.status})")
        return case

    def approve(self, order_id: str, actor: str) -> Case:
        actor = actor.strip() or "persona"
        case = self._require_pending(order_id)
        settlement = case.decision.settlement
        before = case.decision.amount_before
        if settlement == "credit_note":
            credit = self._credit(case)
            case.credit_note = credit
            case.invoice.status = "posted"
            after = eur(case.invoice.imponibile - credit.imponibile)
            note = "Nota di credito registrata insieme alla fattura."
        elif settlement == "backorder":
            case.invoice.status = "posted"
            after = case.invoice.imponibile
            note = "Fattura parziale registrata. Il saldo resta in backorder."
        elif settlement == "replacement":
            after = eur(0)
            note = "Riconsegna richiesta. La fattura contestata non è registrata."
        else:
            case.invoice.status = "posted"
            after = case.invoice.imponibile
            note = "Fattura registrata dopo conferma."
        case.decision.status = "approved"
        case.decision.actor = actor
        case.decision.amount_after = after
        case.decision.note = note
        self.audit.append(
            AuditEntry(
                at=self._stamp(),
                actor=actor,
                action="approve",
                order_id=order_id,
                amount_before=before,
                amount_after=after,
                evidence_ids=list(case.decision.evidence_ids),
                note=note,
            )
        )
        return case

    def reject(self, order_id: str, actor: str, note: str) -> Case:
        actor = actor.strip() or "persona"
        case = self._require_pending(order_id)
        before = case.decision.amount_before
        text = note.strip() or "Proposta rifiutata."
        case.invoice.status = "draft"
        case.decision.status = "rejected"
        case.decision.actor = actor
        case.decision.amount_after = eur(0)
        case.decision.note = text
        self.audit.append(
            AuditEntry(
                at=self._stamp(),
                actor=actor,
                action="reject",
                order_id=order_id,
                amount_before=before,
                amount_after=eur(0),
                evidence_ids=list(case.decision.evidence_ids),
                note=text,
            )
        )
        return case

    def _credit(self, case: Case) -> Invoice:
        line = case.match.lines[0]
        sku = self.skus[line.sku]
        if line.invoice_eur_per_kg != line.contract_eur_per_kg:
            unit = eur(line.invoice_eur_per_kg - line.contract_eur_per_kg)
            description = f"Storno differenza prezzo {sku.name}"
        else:
            unit = line.invoice_eur_per_kg
            description = f"Storno merce contestata {sku.name}"
        qty = case.invoice.qty_kg
        imponibile = eur(qty * unit)
        number = doc_suffix(case.order_id, "NC")
        edi = write_invoic(
            number,
            case.order_id,
            case.shipment.id,
            sku.gtin,
            qty,
            unit,
            imponibile,
            code="381",
        )
        xml = build_invoice(
            self.supplier,
            self.buyer,
            number,
            case.order_id,
            case.shipment.id,
            description,
            qty,
            unit,
            BUSINESS_DATE,
            tipo="TD04",
            linked_invoice=case.invoice.id,
        )
        return Invoice(
            id=number,
            order_id=case.order_id,
            shipment_id=case.shipment.id,
            sku=sku.code,
            qty_kg=qty,
            price_eur_per_kg=unit,
            imponibile=imponibile,
            kind="TD04",
            status="posted",
            raw_edi=edi,
            xml=xml,
        )

    def counts(self) -> dict:
        cases = list(self.cases.values())
        by_kind: dict[str, int] = {}
        for case in cases:
            if case.decision.status == "pending":
                by_kind[case.kind] = by_kind.get(case.kind, 0) + 1
        return {
            "pending": sum(1 for case in cases if case.decision.status == "pending"),
            "posted": sum(1 for case in cases if case.decision.status == "posted"),
            "approved": sum(1 for case in cases if case.decision.status == "approved"),
            "rejected": sum(1 for case in cases if case.decision.status == "rejected"),
            "by_kind": by_kind,
        }

    def to_json(self) -> dict:
        return {
            "banner": "Documento dimostrativo, non fiscale. Nessun collegamento a un ERP e nessun dato cliente reale.",
            "business_date": BUSINESS_DATE.isoformat(),
            "threshold_eur": dec_str(THRESHOLD_EUR, "0.01"),
            "contract_id": self.contract_id,
            "supplier": self.supplier.to_json(),
            "buyer": self.buyer.to_json(),
            "counts": self.counts(),
            "cases": [case.to_json() for case in self.cases.values()],
            "clauses": [clause.to_json() for clause in self.clauses],
            "planning": self._planning,
            "audit": [entry.to_json() for entry in self.audit],
            "architecture": [
                {"step": "MILLED", "meaning": "Il mulino ha prodotto un lotto. Non è ancora una consegna."},
                {"step": "QC_RESULT", "meaning": "Esito sul chicco del lotto. Un esito negativo è evidenza, non una bolla."},
                {"step": "PACKED", "meaning": "Il lotto è a magazzino."},
                {"step": "GOODS_ISSUED", "meaning": "Solo l'uscita merce fa nascere DESADV e fattura."},
            ],
        }
