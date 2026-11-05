"""Grok tool loop. Numbers stay in the office; the model only cites them.

Without XAI_API_KEY, or when the call fails, the checked-in fixture is used.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from brc.model import Proposal, eur


SETTLEMENTS = {"post", "backorder", "credit_note", "replacement"}

SYSTEM = """Sei l'impiegato del back office di Riso Scotti (demo).
Leggi solo i record che gli strumenti ti restituiscono.
Non inventare quantità, prezzi o numeri di documento.
Rispondi in italiano, solo con un oggetto JSON:
{"summary":"...","settlement":"post|backorder|credit_note|replacement","amount_eur":"0.00","evidence_ids":["..."],"clause_ids":["..."]}
evidence_ids e clause_ids devono essere id restituiti dagli strumenti.
temperature è già 0: sii letterale.
"""


def _data_dir():
    from pathlib import Path

    return Path(__file__).resolve().parent.parent / "data"


def load_fixture(order_id: str, task: str) -> Proposal:
    path = _data_dir() / "agent" / f"{order_id}.{task}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return _proposal_from_payload(payload, source=payload.get("source", "offline-fixture"))


def _proposal_from_payload(payload: dict, source: str) -> Proposal:
    settlement = payload["settlement"]
    if settlement not in SETTLEMENTS:
        raise ValueError(f"unknown settlement {settlement}")
    return Proposal(
        task=payload["task"],
        order_id=payload["order_id"],
        summary=payload["summary"].strip(),
        settlement=settlement,
        amount_eur=eur(payload["amount_eur"]),
        evidence_ids=list(payload["evidence_ids"]),
        clause_ids=list(payload["clause_ids"]),
        source=source,
    )


def _tool_schema() -> list[dict]:
    def fn(name: str, description: str) -> dict:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {"order_id": {"type": "string"}},
                    "required": ["order_id"],
                },
            },
        }

    clause = {
        "type": "function",
        "function": {
            "name": "get_clause",
            "description": "Clausola del contratto dimostrativo, per tag o per id.",
            "parameters": {
                "type": "object",
                "properties": {"clause_id": {"type": "string"}, "tag": {"type": "string"}},
            },
        },
    }
    return [
        fn("get_order", "Ordine EDI già interpretato."),
        fn("get_events", "Eventi di stabilimento collegati all'ordine e al lotto."),
        fn("get_shipment", "Bolla DESADV."),
        fn("get_invoice", "Fattura INVOIC e imponibile."),
        fn("get_match", "Esito del confronto ordine, bolla, fattura."),
        fn("get_dispute", "Email di contestazione, se c'è."),
        clause,
    ]


class ToolView:
    """Read-only slice the model is allowed to see."""

    def __init__(self, case: Any, clauses: list[Any]):
        self.case = case
        self.clauses = clauses

    def call(self, name: str, arguments: dict) -> dict:
        case = self.case
        if name == "get_order":
            return case.order.to_json()
        if name == "get_events":
            return {"events": [ev.to_json() for ev in case.events]}
        if name == "get_shipment":
            return case.shipment.to_json()
        if name == "get_invoice":
            payload = case.invoice.to_json()
            payload.pop("xml", None)
            payload.pop("raw_edi", None)
            return payload
        if name == "get_match":
            return case.match.to_json()
        if name == "get_dispute":
            return case.dispute.to_json() if case.dispute else {"dispute": None}
        if name == "get_clause":
            wanted_id = arguments.get("clause_id")
            wanted_tag = arguments.get("tag")
            found = []
            for clause in self.clauses:
                if wanted_id and clause.id != wanted_id:
                    continue
                if wanted_tag and clause.tag != wanted_tag and clause.id != wanted_tag:
                    continue
                if not wanted_id and not wanted_tag:
                    found.append(clause.to_json())
                else:
                    found.append(clause.to_json())
            return {"clauses": found}
        return {"error": f"unknown tool {name}"}


def _post_chat(key: str, messages: list[dict], tools: list[dict]) -> dict:
    base = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1").rstrip("/")
    model = os.environ.get("XAI_MODEL", "grok-4")
    body = json.dumps(
        {"model": model, "temperature": 0, "messages": messages, "tools": tools}
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def _grok_loop(view: ToolView, order_id: str, task: str) -> Proposal:
    key = os.environ["XAI_API_KEY"]
    tools = _tool_schema()
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": f"task={task} order_id={order_id}. Usa gli strumenti, poi il JSON.",
        },
    ]
    for _ in range(6):
        payload = _post_chat(key, messages, tools)
        message = payload["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            messages.append(message)
            for call in tool_calls:
                name = call["function"]["name"]
                raw_args = call["function"].get("arguments") or "{}"
                arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                result = view.call(name, arguments)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            continue
        content = message.get("content") or ""
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end < start:
            raise ValueError("model reply has no JSON object")
        parsed = json.loads(content[start : end + 1])
        parsed.setdefault("task", task)
        parsed.setdefault("order_id", order_id)
        proposal = _proposal_from_payload(parsed, source="grok")
        known = view.case.document_ids({clause.id for clause in view.clauses})
        if any(item not in known for item in proposal.evidence_ids):
            raise ValueError("model cited an id that is not in the file")
        return proposal
    raise RuntimeError("tool loop ended without a final answer")


def propose(case: Any, clauses: list[Any], task: str) -> Proposal:
    offline = os.environ.get("BRC_OFFLINE", "").strip() in {"1", "true", "yes"}
    key = os.environ.get("XAI_API_KEY", "").strip()
    if offline or not key:
        return load_fixture(case.order_id, task)
    try:
        return _grok_loop(ToolView(case, clauses), case.order_id, task)
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError, RuntimeError):
        fallback = load_fixture(case.order_id, task)
        fallback.source = "offline-fixture"
        return fallback
