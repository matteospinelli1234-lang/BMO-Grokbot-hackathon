from pathlib import Path

from brc.edi import parse_desadv, parse_invoic, parse_orders, write_desadv, write_invoic, write_orders
from brc.model import eur, kg

DATA = Path(__file__).resolve().parents[1] / "data" / "edi"


def test_orders_files_roundtrip():
    expected = {
        "ORD1001": ("8001111000011", "200", "1.85"),
        "ORD1002": ("8001111000028", "2000", "1.60"),
        "ORD1003": ("8001111000011", "2200", "1.85"),
        "ORD1004": ("8001111000035", "300", "1.20"),
    }
    for order_id, (gtin, qty, price) in expected.items():
        original = (DATA / f"ORDERS_{order_id}.edi").read_text(encoding="utf-8")
        parsed = parse_orders(original)
        assert parsed["id"] == order_id
        assert parsed["lines"][0]["gtin"] == gtin
        assert parsed["lines"][0]["qty_kg"] == kg(qty)
        assert parsed["lines"][0]["price"] == eur(price)
        assert write_orders(order_id, gtin, kg(qty), eur(price)) == original


def test_desadv_and_invoic_samples():
    desadv = (DATA / "DESADV_DES1001.edi").read_text(encoding="utf-8")
    invoic = (DATA / "INVOIC_INV1001.edi").read_text(encoding="utf-8")
    assert write_desadv("DES1001", "ORD1001", "8001111000011", kg("200"), "LOT-CAR-17") == desadv
    assert (
        write_invoic("INV1001", "ORD1001", "DES1001", "8001111000011", kg("200"), eur("1.85"), eur("370.00"))
        == invoic
    )
    parsed_des = parse_desadv(desadv)
    assert parsed_des["order_id"] == "ORD1001"
    assert parsed_des["qty_kg"] == kg("200")
    assert parsed_des["lot"] == "LOT-CAR-17"
    parsed_inv = parse_invoic(invoic)
    assert parsed_inv["code"] == "380"
    assert parsed_inv["amount"] == eur("370.00")
    assert parsed_inv["shipment_id"] == "DES1001"
