"""End-to-end flow against the offline LocalMemory backend (no API keys needed).

    pytest -q
"""
import importlib
import os
import sys

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DEJA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HINDSIGHT_URL", "")
    monkeypatch.setenv("GROQ_API_KEY", "")
    for m in [m for m in sys.modules if m.startswith("deja")]:
        del sys.modules[m]
    main = importlib.import_module("deja.main")
    return TestClient(main.app)


def _open(c, **body):
    r = c.post("/api/incidents", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_recurring_incident_is_recognised_and_past_failures_are_flagged(client):
    assert client.post("/api/demo/seed").json()["retained"] == 12
    inc = _open(client, scenario="checkout-pool")
    t = client.post(f"/api/incidents/{inc}/triage").json()["triage"]

    deja, base = t["deja"], t["baseline"]
    assert deja["seen_before"] is True
    assert {"INC-2031", "INC-2064"} <= {m["id"] for m in deja["matched_incidents"]}
    assert "bulk-export" in deja["likely_root_cause"]
    assert any("failover" in d["action"].lower() for d in deja["do_not"])
    # The stateless agent recommends exactly what failed before.
    assert base["seen_before"] is False
    assert any("pool size" in a["action"].lower() for a in base["first_actions"])


def test_agent_learns_a_brand_new_failure_mode(client):
    first = _open(client, scenario="ledger-wal")
    t1 = client.post(f"/api/incidents/{first}/triage").json()["triage"]["deja"]
    assert t1["seen_before"] is False and t1["confidence"] <= 35

    r = client.post(f"/api/incidents/{first}/resolve", json={
        "root_cause": "Inactive Debezium replication slot debezium_ledger retained WAL until disk filled",
        "fix": "Restart Debezium connector, set max_slot_wal_keep_size=20GB",
        "failed": "Expanding the PVC: only bought time", "resolved_by": "Sara Lindqvist",
        "ttr_minutes": 66, "verdict": "wrong", "notes": "it was the replication slot",
    })
    assert r.status_code == 200 and r.json()["status"] == "resolved"

    second = _open(client, service="ledger-service", severity="SEV2",
                   alert="ledger-service Postgres: No space left on device writing pg_wal",
                   logs="replication slot debezium_ledger inactive")
    t2 = client.post(f"/api/incidents/{second}/triage").json()["triage"]["deja"]
    assert t2["seen_before"] is True
    assert "debezium" in t2["likely_root_cause"].lower()
    assert any("pvc" in d["action"].lower() for d in t2["do_not"])
    assert t2["page"]["who"] == "Sara Lindqvist"

    stats = client.get("/api/stats").json()
    assert stats["graded"] == 1 and stats["accuracy"] == 0


def test_team_rules_are_enforced(client):
    client.post("/api/rules", json={"name": "No primary failover", "content": "Never fail over the Postgres primary for pool exhaustion."})
    inc = _open(client, service="checkout-api", alert="HikariPool connection timeout")
    deja = client.post(f"/api/incidents/{inc}/triage").json()["triage"]["deja"]
    assert any(d["evidence"] == "team rule" for d in deja["do_not"])


def test_bad_input_is_rejected(client):
    assert client.post("/api/incidents", json={"service": "x"}).status_code == 400
    assert client.post("/api/incidents", json={"scenario": "nope"}).status_code == 400
    assert client.post("/api/incidents/INC-1/triage").status_code == 404
    assert client.post("/api/ask", json={"question": ""}).status_code == 422


def test_runbook_and_ask_work_offline(client):
    client.post("/api/demo/seed")
    rb = client.get("/api/runbook/checkout-api").json()
    assert "INC-2031" in rb["content"]
    ans = client.post("/api/ask", json={"question": "What went wrong with DNS lookups for payments?"}).json()
    assert "INC-2110" in ans["answer"]


def test_llm_json_repair():
    from deja.llm import extract_json
    assert extract_json('<think>hmm</think>```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! {"a": {"b": 2}} hope that helps') == {"a": {"b": 2}}
    assert extract_json("not json") is None
