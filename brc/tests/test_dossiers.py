import os

from brc.office import Office

os.environ["BRC_OFFLINE"] = "1"


def office() -> Office:
    return Office()


def test_four_dossiers_without_network():
    book = office()
    clean = book.cases["ORD1001"]
    short = book.cases["ORD1002"]
    price = book.cases["ORD1003"]
    quality = book.cases["ORD1004"]

    assert clean.decision.status == "posted"
    assert clean.decision.actor == "sistema"
    assert clean.invoice.status == "posted"
    assert clean.invoice.imponibile and clean.gate.human is False
    assert "TD01" in clean.invoice.xml
    assert "DatiOrdineAcquisto" in clean.invoice.xml
    assert "DatiDDT" in clean.invoice.xml
    assert clean.order_clerk.source == "offline-fixture"
    assert clean.dispute is None

    assert short.shipment.qty_kg < short.order.lines[0].qty_kg
    assert any(event.kind == "MILLED" and event.order_id == "ORD1002" for event in short.events)
    assert any(event.kind == "GOODS_ISSUED" for event in short.events)
    assert short.match.status == "short_delivery"
    assert short.decision.status == "pending"
    assert short.dispute_clerk.settlement == "backorder"
    assert "ORD1002" in short.dispute_clerk.summary
    assert "DES1002" in short.dispute_clerk.summary

    assert price.match.status == "price_variance"
    assert price.decision.status == "pending"
    assert price.dispute_clerk.settlement == "credit_note"
    assert "price-restrictions" in price.dispute_clerk.evidence_ids

    assert quality.match.status == "matched"
    assert quality.gate.human is True
    assert quality.decision.status == "pending"
    assert any(event.id == "LOT-ROM-441-QC" and "negativo" in event.detail for event in quality.events)
    assert "LOT-ROM-441-QC" in quality.dispute_clerk.evidence_ids

    known_clauses = {clause.id for clause in book.clauses}
    for case in book.cases.values():
        known = case.document_ids(known_clauses)
        for proposal in (case.order_clerk, case.dispute_clerk):
            if proposal is None:
                continue
            assert set(proposal.evidence_ids) <= known
            assert proposal.summary
        assert any(event.kind == "GOODS_ISSUED" for event in case.events)

    counts = book.counts()
    assert counts["posted"] == 1
    assert counts["pending"] == 3
    assert counts["by_kind"] == {"short": 1, "price": 1, "quality": 1}
    assert book._planning
    assert all(row["basis"].startswith("media") for row in book._planning)


def test_approve_price_credit_and_reject_short():
    book = office()
    priced = book.approve("ORD1003", "Marta Riva")
    assert priced.decision.status == "approved"
    assert priced.decision.actor == "Marta Riva"
    assert priced.credit_note is not None
    assert priced.credit_note.kind == "TD04"
    assert "TD04" in priced.credit_note.xml
    assert priced.credit_note.imponibile == priced.match.exposure_eur
    assert priced.decision.amount_before == priced.gate.amount_eur
    assert priced.decision.amount_after == priced.invoice.imponibile - priced.credit_note.imponibile
    assert book.audit[-1].action == "approve"
    assert book.audit[-1].evidence_ids == priced.decision.evidence_ids

    rejected = book.reject("ORD1002", "Marta Riva", "Serve il saldo prima di fatturare.")
    assert rejected.decision.status == "rejected"
    assert rejected.invoice.status == "draft"
    assert rejected.credit_note is None
    assert rejected.decision.amount_after == 0
    assert book.audit[-1].amount_after == rejected.decision.amount_after
