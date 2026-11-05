"""Local dossier page for the synthetic back office."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from brc.office import Office

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"

app = FastAPI(title="B.R.C. back office demo")
office = Office()


def reset_office() -> Office:
    global office
    office = Office()
    return office


class ActorBody(BaseModel):
    actor: str = Field(default="Marta Riva", max_length=80)
    note: str = Field(default="", max_length=400)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/state")
def state() -> dict:
    return office.to_json()


@app.post("/api/reset")
def reset() -> dict:
    reset_office()
    return office.to_json()


@app.post("/api/cases/{order_id}/approve")
def approve(order_id: str, body: ActorBody) -> dict:
    if order_id not in office.cases:
        raise HTTPException(status_code=404, detail="Pratica sconosciuta")
    try:
        office.approve(order_id, body.actor)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return office.to_json()


@app.post("/api/cases/{order_id}/reject")
def reject(order_id: str, body: ActorBody) -> dict:
    if order_id not in office.cases:
        raise HTTPException(status_code=404, detail="Pratica sconosciuta")
    try:
        office.reject(order_id, body.actor, body.note)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return office.to_json()
