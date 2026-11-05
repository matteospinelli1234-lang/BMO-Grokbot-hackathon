import json
from pathlib import Path

from brc.match import match_line, primary_flag
from brc.model import eur, kg

DATA = Path(__file__).resolve().parents[1] / "data" / "threeway"


def test_threeway_fixtures():
    for path in sorted(DATA.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        line = match_line(
            "SKU",
            kg(payload["ordered_kg"]),
            kg(payload["shipped_kg"]),
            kg(payload["billed_kg"]),
            eur(payload["contract_eur_per_kg"]),
            eur(payload["invoice_eur_per_kg"]),
        )
        assert primary_flag(line.flags) == payload["expect"]
        if payload["expect"] == "matched":
            assert line.exposure_eur == eur("0")
        if payload["expect"] == "short_delivery":
            assert line.exposure_eur == eur("2400.00")
        if payload["expect"] == "price_variance":
            assert line.exposure_eur == eur("1210.00")
