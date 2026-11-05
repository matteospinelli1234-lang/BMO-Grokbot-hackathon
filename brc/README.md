# B.R.C. back office

Zero-touch back office on synthetic data for the Riso Scotti brief. A retail
order becomes a stock check, plant events, a despatch, an Italian e-invoice,
a three-way match, and, when the case is a dispute, a settlement that cites
those documents.

This is not an ERP adapter. There is no customer data and no machine connection.
The plant shows up only as an append-only event log: `MILLED`, `QC_RESULT`,
`PACKED`, `GOODS_ISSUED`. Only `GOODS_ISSUED` creates a DESADV and an invoice.

## Run

```bash
cd brc
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest
uvicorn app:app --port 8080
```

Open http://127.0.0.1:8080. The banner says the documents are demonstrative and
not fiscal.

## What the page does

- **ORD1001** Carnaroli, 370 euro, full match, under the 500 euro threshold. The
  system posts it. Nobody clicks.
- **ORD1002** Arborio ordered 2000 kg, 500 kg on hand. A mill event covers the
  rest. The invoice bills only what left the warehouse. The retailer email asks
  about the shortage. Proposed settlement: post the partial invoice and keep
  the rest on backorder. A person confirms.
- **ORD1003** the draft invoice uses the list price. The contract clause
  `price-restrictions` does not. Proposed settlement: credit note for the
  difference, 1210 euro. A person confirms.
- **ORD1004** the Roma lot already failed QC and was shipped anyway. Quantity
  and price match, and 360 euro is under the threshold, but a quality write-off
  does not post itself. Proposed settlement: credit the whole line.

Approving a credit note writes a FatturaPA `TD04` and an EDIFACT credit
(`BGM+381`). Rejecting leaves the draft unposted. The audit line stores the
actor, the evidence ids, and the amounts before and after.

## Grok

Clerks call the xAI chat API with tools (`get_order`, `get_events`,
`get_shipment`, `get_invoice`, `get_match`, `get_dispute`, `get_clause`) at
temperature 0. Set `XAI_API_KEY` to use it. `XAI_MODEL` defaults to `grok-4`.

With no key, or if the call fails, the page uses the JSON in `data/agent/`.
Tests set `BRC_OFFLINE=1`. Dispute emails live in `data/emails/`. To replace
them with a live generation:

```bash
XAI_API_KEY=... python scripts/regenerate_emails.py
```

## Public sources, and what we did not copy

- EDIFACT shape follows the public GS1 / EdiFabric D96A examples (`BGM`, `NAD`,
  `LIN`, `QTY`, `PRI`, `RFF+ON`). The rice messages in `data/edi/` are original.
  Parsing is a small Python subset, not the .NET EdiFabric SDK.
- FatturaPA XML is built with [python-a38](https://github.com/Truelite/python-a38).
- Clause *tags* follow CUAD categories that matter in a supply argument (price
  restrictions, warranty, limitation of liability, termination, governing law).
  The clause text is original. The CUAD corpus is not in this repo.
- `data/threeway/` is the match baseline: matched, short quantity, price variance.

## Stretch on the same page

- Exception counts by open kind (short, price, quality).
- A mill sketch: average of the last four synthetic weeks, times 1.10. It is
  labeled as a heuristic.
- GrainSet is CC BY-NC-SA 4.0 (non-commercial, attribution, share-alike) and the
  rice pack is about 3 GB, so it is not downloaded. Quality evidence is the
  `QC_RESULT` event. The page states the licence.

VAT numbers, GLNs, and the buyer “Grande Distribuzione Nord” are fictional.
