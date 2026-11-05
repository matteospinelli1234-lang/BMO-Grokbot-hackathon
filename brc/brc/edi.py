"""EDIFACT D96A subset: ORDERS, DESADV, INVOIC. No EdiFabric dependency."""

from __future__ import annotations

from decimal import Decimal

from brc.model import D, eur, kg


SUPPLIER_GLN = "8001111000008"
BUYER_GLN = "8002222000003"
SUPPLIER_NAME = "RISO SCOTTI DEMO"
BUYER_NAME = "GRANDE DISTRIBUZIONE NORD"


def parse_segments(text: str) -> list[list[str]]:
    body = text.lstrip("\ufeff")
    if body.startswith("UNA"):
        body = body.split("'", 1)[1]
    segments: list[list[str]] = []
    for raw in body.split("'"):
        raw = raw.strip()
        if raw:
            segments.append(raw.split("+"))
    return segments


def _qty_field(segment: list[str]) -> tuple[str, Decimal]:
    parts = segment[1].split(":")
    return parts[0], kg(parts[1])


def _price_field(segment: list[str]) -> Decimal:
    parts = segment[1].split(":")
    return eur(parts[1])


def parse_orders(text: str) -> dict:
    lines = []
    order_id = ""
    current: dict | None = None
    for seg in parse_segments(text):
        tag = seg[0]
        if tag == "BGM":
            order_id = seg[2]
        elif tag == "LIN":
            if current:
                lines.append(current)
            current = {"gtin": seg[3].split(":")[0], "qty_kg": None, "price": None}
        elif tag == "QTY" and current is not None:
            qual, qty = _qty_field(seg)
            if qual == "21":
                current["qty_kg"] = qty
        elif tag == "PRI" and current is not None:
            current["price"] = _price_field(seg)
        elif tag == "RFF" and seg[1].startswith("ON:"):
            order_id = seg[1].split(":", 1)[1]
    if current:
        lines.append(current)
    if not order_id or not lines:
        raise ValueError("ORDERS message has no order id or lines")
    return {"id": order_id, "lines": lines}


def parse_desadv(text: str) -> dict:
    doc_id = ""
    order_id = ""
    lot = ""
    gtin = ""
    qty = None
    for seg in parse_segments(text):
        tag = seg[0]
        if tag == "BGM":
            doc_id = seg[2]
        elif tag == "RFF" and seg[1].startswith("ON:"):
            order_id = seg[1].split(":", 1)[1]
        elif tag == "RFF" and seg[1].startswith("BT:"):
            lot = seg[1].split(":", 1)[1]
        elif tag == "LIN":
            gtin = seg[3].split(":")[0]
        elif tag == "QTY":
            qual, parsed = _qty_field(seg)
            if qual == "12":
                qty = parsed
    return {"id": doc_id, "order_id": order_id, "lot": lot, "gtin": gtin, "qty_kg": qty}


def parse_invoic(text: str) -> dict:
    doc_id = ""
    order_id = ""
    shipment_id = ""
    doc_code = ""
    gtin = ""
    qty = None
    price = None
    amount = None
    for seg in parse_segments(text):
        tag = seg[0]
        if tag == "BGM":
            doc_code = seg[1]
            doc_id = seg[2]
        elif tag == "RFF" and seg[1].startswith("ON:"):
            order_id = seg[1].split(":", 1)[1]
        elif tag == "RFF" and seg[1].startswith("DQ:"):
            shipment_id = seg[1].split(":", 1)[1]
        elif tag == "LIN":
            gtin = seg[3].split(":")[0]
        elif tag == "QTY":
            qual, parsed = _qty_field(seg)
            if qual == "47":
                qty = parsed
        elif tag == "PRI":
            price = _price_field(seg)
        elif tag == "MOA" and seg[1].startswith("203:"):
            amount = eur(seg[1].split(":", 1)[1])
    return {
        "id": doc_id,
        "code": doc_code,
        "order_id": order_id,
        "shipment_id": shipment_id,
        "gtin": gtin,
        "qty_kg": qty,
        "price": price,
        "amount": amount,
    }


def _edi_qty(value: Decimal) -> str:
    quantized = kg(value)
    if quantized == quantized.to_integral():
        return str(int(quantized))
    return format(quantized, "f")


def _edi_price(value: Decimal) -> str:
    return f"{eur(value):.2f}"


def _message(ref: str, sender: str, receiver: str, hhmm: str, message_type: str, body: list[str]) -> str:
    inner = [f"UNH+1+{message_type}:D:96A:UN", *body]
    inner.append(f"UNT+{len(inner) + 1}+1")
    parts = [
        "UNA:+.? ",
        f"UNB+UNOC:3+{sender}:14+{receiver}:14+261105:{hhmm}+{ref}",
        *inner,
        f"UNZ+1+{ref}",
    ]
    return "\n".join(part + "'" for part in parts) + "\n"


def write_orders(order_id: str, gtin: str, qty_kg: Decimal, price: Decimal) -> str:
    body = [
        f"BGM+220+{order_id}+9",
        "DTM+137:20261105:102",
        f"NAD+BY+{BUYER_GLN}::9++{BUYER_NAME}",
        f"NAD+SU+{SUPPLIER_GLN}::9++{SUPPLIER_NAME}",
        f"LIN+1++{gtin}:SRV",
        f"QTY+21:{_edi_qty(qty_kg)}:KGM",
        f"PRI+AAA:{_edi_price(price)}:CA::1:KGM",
        f"RFF+ON:{order_id}",
        "UNS+S",
    ]
    times = {"ORD1001": "0800", "ORD1002": "0810", "ORD1003": "0820", "ORD1004": "0830"}
    return _message(order_id, BUYER_GLN, SUPPLIER_GLN, times.get(order_id, "0800"), "ORDERS", body)


def write_desadv(des_id: str, order_id: str, gtin: str, qty_kg: Decimal, lot: str) -> str:
    body = [
        f"BGM+351+{des_id}+9",
        "DTM+137:20261105:102",
        "DTM+11:20261105:102",
        f"RFF+ON:{order_id}",
        f"NAD+SU+{SUPPLIER_GLN}::9++{SUPPLIER_NAME}",
        f"NAD+BY+{BUYER_GLN}::9++{BUYER_NAME}",
        f"LIN+1++{gtin}:SRV",
        f"QTY+12:{_edi_qty(qty_kg)}:KGM",
        f"RFF+BT:{lot}",
        "UNS+S",
    ]
    return _message(des_id, SUPPLIER_GLN, BUYER_GLN, "1100", "DESADV", body)


def write_invoic(
    inv_id: str,
    order_id: str,
    des_id: str,
    gtin: str,
    qty_kg: Decimal,
    price: Decimal,
    amount: Decimal,
    code: str = "380",
) -> str:
    body = [
        f"BGM+{code}+{inv_id}+9",
        "DTM+137:20261105:102",
        f"RFF+ON:{order_id}",
        f"RFF+DQ:{des_id}",
        f"NAD+SU+{SUPPLIER_GLN}::9++{SUPPLIER_NAME}",
        f"NAD+BY+{BUYER_GLN}::9++{BUYER_NAME}",
        f"LIN+1++{gtin}:SRV",
        f"QTY+47:{_edi_qty(qty_kg)}:KGM",
        f"PRI+AAA:{_edi_price(price)}:CA::1:KGM",
        f"MOA+203:{_edi_price(amount)}",
        "UNS+S",
    ]
    return _message(inv_id, SUPPLIER_GLN, BUYER_GLN, "1200", "INVOIC", body)


def doc_suffix(order_id: str, prefix: str) -> str:
    number = order_id.removeprefix("ORD")
    return f"{prefix}{number}"


def as_decimal(value: str) -> Decimal:
    return D(value)
