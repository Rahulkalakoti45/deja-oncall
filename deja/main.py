"""HTTP API + static UI.  Run: uvicorn deja.main:app --reload"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import Deja
from .config import ROOT, settings
from .llm import LLM
from .memory import build_memory
from .seed_data import SCENARIOS, TEAM
from .store import IncidentStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

memory = build_memory(settings)
deja = Deja(memory, LLM(settings), IncidentStore(settings.data_dir / "incidents.json"))

app = FastAPI(title="Deja - on-call memory agent")
WEB = ROOT / "web"


class OpenBody(BaseModel):
    scenario: str | None = None
    service: str | None = None
    severity: str | None = None
    title: str | None = None
    alert: str | None = None
    logs: str | None = None


class ResolveBody(BaseModel):
    root_cause: str = Field(default="", max_length=2000)
    fix: str = Field(default="", max_length=2000)
    failed: str = Field(default="", max_length=2000)
    resolved_by: str = Field(default="", max_length=100)
    ttr_minutes: int | None = Field(default=None, ge=1, le=10000)
    verdict: str | None = None
    notes: str = Field(default="", max_length=1000)


class AskBody(BaseModel):
    question: str = Field(min_length=2, max_length=500)


class TeachBody(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    content: str = Field(min_length=5, max_length=600)


def _guard(fn, *a, **kw) -> Any:
    try:
        return fn(*a, **kw)
    except KeyError:
        raise HTTPException(404, "incident not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # memory backend down etc. - surface a readable error to the UI
        logging.exception("request failed")
        raise HTTPException(502, f"{type(e).__name__}: {str(e)[:300]}")


@app.get("/api/status")
def status():
    return {
        "memory_backend": memory.name, "bank_id": settings.bank_id if memory.name == "hindsight" else None,
        "llm": settings.groq_model if settings.llm_enabled else None,
        "scenarios": [{k: s[k] for k in ("key", "label", "service", "severity")} for s in SCENARIOS],
        "team": TEAM,
    }


@app.get("/api/stats")
def stats():
    return _guard(deja.stats)


@app.get("/api/incidents")
def incidents():
    return deja.store.all()[::-1]


@app.get("/api/incidents/{inc_id}")
def incident(inc_id: str):
    inc = deja.store.get(inc_id)
    if not inc:
        raise HTTPException(404, "incident not found")
    return inc


@app.post("/api/incidents")
def open_incident(body: OpenBody):
    return _guard(deja.open_incident, body.scenario, body.model_dump())


@app.post("/api/incidents/{inc_id}/triage")
def triage(inc_id: str):
    return _guard(deja.triage, inc_id)


@app.post("/api/incidents/{inc_id}/resolve")
def resolve(inc_id: str, body: ResolveBody):
    return _guard(deja.resolve, inc_id, body.model_dump())


@app.get("/api/runbook/{service}")
def runbook(service: str):
    return _guard(deja.runbook, service)


@app.post("/api/ask")
def ask(body: AskBody):
    return _guard(deja.ask, body.question)


@app.get("/api/rules")
def rules():
    return _guard(deja._safe_directives)


@app.post("/api/rules")
def teach(body: TeachBody):
    return _guard(deja.teach, body.name, body.content)


@app.get("/api/memories")
def memories():
    return _guard(lambda: [m.to_dict() for m in memory.recent(40)])


@app.post("/api/demo/seed")
def seed():
    return {"retained": _guard(deja.seed_history)}


@app.post("/api/demo/reset")
def reset():
    _guard(deja.reset)
    return {"ok": True}


app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")
