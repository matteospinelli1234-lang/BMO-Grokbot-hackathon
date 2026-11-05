from fastapi.testclient import TestClient

import app as demo


def test_page_and_approval_roundtrip():
    demo.reset_office()
    client = TestClient(demo.app)
    home = client.get("/")
    assert home.status_code == 200
    assert "non fiscale" in home.text
    assert "GrainSet" in home.text

    state = client.get("/api/state").json()
    assert state["counts"]["pending"] == 3
    assert state["counts"]["posted"] == 1
    ids = {case["order_id"] for case in state["cases"]}
    assert ids == {"ORD1001", "ORD1002", "ORD1003", "ORD1004"}

    approved = client.post("/api/cases/ORD1004/approve", json={"actor": "Marta Riva"}).json()
    quality = next(case for case in approved["cases"] if case["order_id"] == "ORD1004")
    assert quality["decision"]["status"] == "approved"
    assert quality["credit_note"]["kind"] == "TD04"
    assert quality["decision"]["amount_after"] == "0.00"
    assert approved["counts"]["pending"] == 2

    blocked = client.post("/api/cases/ORD1001/approve", json={"actor": "Marta Riva"})
    assert blocked.status_code == 409

    missing = client.post("/api/cases/ORD9999/approve", json={"actor": "Marta Riva"})
    assert missing.status_code == 404
