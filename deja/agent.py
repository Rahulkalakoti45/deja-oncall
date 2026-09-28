"""Deja's brain: triage with memory, learn from resolutions, maintain runbooks.

Every triage produces two answers for the same alert:
  * `baseline` - the same LLM with no memory (what a stateless agent says)
  * `deja`     - grounded in Hindsight recall + team directives
The UI shows them side by side, so the value of memory is visible at a glance.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from .llm import LLM
from .memory import Memory, MemoryBackend
from .seed_data import HISTORY, SCENARIOS, postmortem_metadata, postmortem_text
from .store import IncidentStore

log = logging.getLogger("deja.agent")

TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "seen_before": {"type": "boolean"},
        "matched_incidents": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "date": {"type": "string"}, "why": {"type": "string"}}}},
        "likely_root_cause": {"type": "string"},
        "confidence": {"type": "integer"},
        "first_actions": {"type": "array", "items": {"type": "object", "properties": {
            "action": {"type": "string"}, "why": {"type": "string"}, "evidence": {"type": "string"}}}},
        "do_not": {"type": "array", "items": {"type": "object", "properties": {
            "action": {"type": "string"}, "why": {"type": "string"}, "evidence": {"type": "string"}}}},
        "page": {"type": "object", "properties": {"who": {"type": "string"}, "why": {"type": "string"}}},
    },
    "required": ["headline", "seen_before", "likely_root_cause", "confidence", "first_actions", "do_not"],
}

_SCHEMA_HINT = json.dumps({
    "headline": "one sentence an engineer reads at 2am",
    "seen_before": True,
    "matched_incidents": [{"id": "INC-0000", "date": "YYYY-MM-DD", "why": "which signals match"}],
    "likely_root_cause": "string",
    "confidence": 0,
    "first_actions": [{"action": "concrete command or step", "why": "string", "evidence": "INC id or 'general practice'"}],
    "do_not": [{"action": "string", "why": "what happened last time", "evidence": "INC id or rule name"}],
    "page": {"who": "person or team", "why": "string"},
}, indent=1)

DEJA_SYSTEM = f"""You are Deja, an on-call SRE agent with long-term memory of this team's production incidents.
The MEMORIES block was retrieved from the team's Hindsight memory bank for this alert.
Rules:
- Team-specific claims (root causes, fixes, people, dates) must come from MEMORIES; cite incident IDs as evidence.
- If a memory says an action failed or made things worse, it MUST appear in do_not.
- TEAM RULES are mandatory and override everything else.
- Match on signals (error strings, time of day, service, recent changes), not just service name.
- If no memory is genuinely relevant: seen_before=false, confidence <= 35, say so plainly and give sound general steps.
- first_actions: at most 4, ordered, concrete (commands, flags, file names). do_not: at most 4.
- page.who: the person who fixed the matched incident fastest, if any.
JSON shape:
{_SCHEMA_HINT}"""

BASELINE_SYSTEM = f"""You are a generic on-call assistant. You have NO access to this company's incident history,
people, or past decisions. Triage the alert with general SRE knowledge only.
seen_before must be false and matched_incidents empty. Evidence is always 'general practice'. page.who is 'on-call lead'.
JSON shape:
{_SCHEMA_HINT}"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _incident_id_of(m: Memory) -> str | None:
    if m.metadata.get("incident_id"):
        return m.metadata["incident_id"]
    if m.document_id and m.document_id.startswith("INC-"):
        return m.document_id
    hit = re.search(r"INC-\d+", m.text)
    return hit.group(0) if hit else None


def _alert_query(inc: dict[str, Any]) -> str:
    return f"{inc['service']} {inc['title']}. {inc['alert']} " + " ".join(inc.get("logs") or [])


class Deja:
    def __init__(self, memory: MemoryBackend, llm: LLM, store: IncidentStore):
        self.memory, self.llm, self.store = memory, llm, store

    # ------------------------------------------------------------------ incidents

    def open_incident(self, scenario_key: str | None = None, custom: dict[str, Any] | None = None) -> dict[str, Any]:
        if scenario_key:
            sc = next((s for s in SCENARIOS if s["key"] == scenario_key), None)
            if not sc:
                raise ValueError(f"unknown scenario {scenario_key}")
            hh, mm = sc["hour"].split(":")
            started = _now().replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
            base = {k: sc[k] for k in ("service", "severity", "title", "alert", "logs")}
            base["scenario"] = sc["key"]
            base["prefill"] = sc["resolution"]
        else:
            c = custom or {}
            if not c.get("service") or not c.get("alert"):
                raise ValueError("service and alert are required")
            started = _now().replace(microsecond=0)
            base = {
                "service": c["service"].strip(), "severity": c.get("severity") or "SEV3",
                "title": (c.get("title") or c["alert"][:80]).strip(), "alert": c["alert"].strip(),
                "logs": [l for l in (c.get("logs") or "").splitlines() if l.strip()] if isinstance(c.get("logs"), str) else (c.get("logs") or []),
                "scenario": None, "prefill": None,
            }
        inc = {"id": self.store.next_id(), "status": "open", "source": "live",
               "started_at": started.isoformat().replace("+00:00", "Z"), **base}
        return self.store.upsert(inc)

    def triage(self, inc_id: str) -> dict[str, Any]:
        inc = self.store.get(inc_id)
        if not inc:
            raise KeyError(inc_id)
        t0 = time.perf_counter()
        memories = self._safe_recall(_alert_query(inc))
        recall_ms = int((time.perf_counter() - t0) * 1000)
        rules = self._safe_directives()

        t1 = time.perf_counter()
        deja, engine = self._deja_answer(inc, memories, rules)
        deja_ms = int((time.perf_counter() - t1) * 1000)
        baseline = self._baseline_answer(inc)

        inc["triage"] = {
            "deja": deja, "baseline": baseline, "engine": engine,
            "memories": [m.to_dict() for m in memories[:8]], "rules": rules,
            "recall_ms": recall_ms, "deja_ms": deja_ms, "at": _now().isoformat(),
        }
        return self.store.upsert(inc)

    def resolve(self, inc_id: str, body: dict[str, Any]) -> dict[str, Any]:
        inc = self.store.get(inc_id)
        if not inc:
            raise KeyError(inc_id)
        failed = [f.strip() for f in re.split(r"[\n;|]+", body.get("failed") or "") if f.strip()]
        try:
            ttr = max(1, int(body.get("ttr_minutes") or 0))
        except (TypeError, ValueError):
            ttr = 1
        record = {
            "id": inc["id"], "service": inc["service"], "severity": inc["severity"], "started_at": inc["started_at"],
            "title": inc["title"], "symptoms": inc["alert"] + " " + " ".join(inc.get("logs") or []),
            "root_cause": (body.get("root_cause") or "").strip() or "unknown",
            "fix": (body.get("fix") or "").strip() or "unknown", "failed": failed,
            "resolved_by": (body.get("resolved_by") or "on-call").strip(), "ttr_minutes": ttr,
        }
        verdict = body.get("verdict") if body.get("verdict") in ("correct", "partial", "wrong") else None
        items = [{
            "content": postmortem_text(record), "context": "incident postmortem", "timestamp": inc["started_at"],
            "document_id": inc["id"], "metadata": postmortem_metadata(record),
            "tags": [f"service:{inc['service']}", f"severity:{inc['severity']}", "type:postmortem"],
        }]
        deja = (inc.get("triage") or {}).get("deja") or {}
        if verdict:
            items.append({
                "content": (
                    f"Engineer feedback on Deja's triage of {inc['id']} ({inc['service']}): verdict={verdict}. "
                    f"Deja predicted root cause: {deja.get('likely_root_cause', 'n/a')}. "
                    f"Actual root cause: {record['root_cause']}. "
                    + (f"Correction from {record['resolved_by']}: {body.get('notes')}" if body.get("notes") else "")
                ),
                "context": "engineer feedback", "timestamp": _now().isoformat(), "document_id": f"{inc['id']}-feedback",
                "metadata": {"incident_id": inc["id"], "verdict": verdict, "service": inc["service"]},
                "tags": [f"service:{inc['service']}", "type:feedback"],
            })
        t0 = time.perf_counter()
        self.memory.retain_many(items)
        retain_ms = int((time.perf_counter() - t0) * 1000)
        self.memory.refresh_runbook(inc["service"])

        inc.update({"status": "resolved", "resolution": {**record, "failed": failed, "verdict": verdict,
                                                         "notes": body.get("notes") or ""},
                    "ttr_minutes": ttr, "retain_ms": retain_ms, "resolved_at": _now().isoformat()})
        return self.store.upsert(inc)

    # ------------------------------------------------------------------ knowledge

    def runbook(self, service: str) -> dict[str, Any]:
        content = None
        source = "mental-model"
        try:
            content = self.memory.runbook(service)
        except Exception as e:
            log.warning("runbook mental model failed: %s", e)
        if not content:
            source = "reflect"
            q = (f"Write the operational runbook for {service}: known failure modes with symptoms, the fix that worked "
                 f"(cite incident IDs), actions that did NOT work, recurring triggers, and who to page. Use markdown.")
            try:
                r = self.memory.reflect(q, tags=[f"service:{service}"])
                content = r.text if r else None
            except Exception as e:
                log.warning("runbook reflect failed: %s", e)
        if not content:
            source = "recall+llm"
            mems = self._safe_recall(f"{service} incident root cause fix", tags=[f"service:{service}"])
            mems = [m for m in mems if m.metadata.get("service", service) == service] or mems
            content = self.llm.text(
                "You are a senior SRE. Write a concise markdown runbook from these incident memories only. "
                "Sections: Known failure modes, What works, What NOT to do, Who to page. Cite incident IDs.",
                self._format_memories(mems)) if mems else None
            if not content:
                source = "memories"
                content = self._runbook_from_metadata(service, mems)
        return {"service": service, "content": content, "source": source}

    def ask(self, question: str) -> dict[str, Any]:
        question = question.strip()[:500]
        try:
            r = self.memory.reflect(question, context="Question from the on-call engineer.")
            if r and r.text:
                return {"answer": r.text, "engine": "hindsight-reflect",
                        "based_on": [m.to_dict() for m in r.based_on[:6]]}
        except Exception as e:
            log.warning("reflect failed: %s", e)
        mems = self._safe_recall(question)
        answer = self.llm.text(
            "You are Deja, the on-call team's memory. Answer only from these memories; cite incident IDs; "
            "say plainly if the memories don't cover it.",
            f"MEMORIES:\n{self._format_memories(mems)}\n\nQUESTION: {question}") if mems else None
        if not answer:
            answer = ("Here is what I remember that looks relevant:\n\n" +
                      "\n".join(f"- {m.text.splitlines()[0]}" for m in mems[:5])) if mems else \
                "I don't have any memories about that yet."
        return {"answer": answer, "engine": "recall+llm" if self.llm.enabled else "recall",
                "based_on": [m.to_dict() for m in mems[:6]]}

    def teach(self, name: str, content: str) -> list[dict[str, Any]]:
        name, content = name.strip()[:80], content.strip()[:600]
        if not name or not content:
            raise ValueError("name and rule are required")
        self.memory.add_directive(name, content)
        return self._safe_directives()

    # ------------------------------------------------------------------ demo data

    def seed_history(self) -> int:
        items = []
        for h in HISTORY:
            self.store.upsert({
                "id": h["id"], "service": h["service"], "severity": h["severity"], "title": h["title"],
                "alert": h["symptoms"], "logs": [], "started_at": h["started_at"], "status": "resolved",
                "source": "history", "ttr_minutes": h["ttr_minutes"], "resolution": {**h, "verdict": None},
            })
            items.append({
                "content": postmortem_text(h), "context": "incident postmortem", "timestamp": h["started_at"],
                "document_id": h["id"], "metadata": postmortem_metadata(h),
                "tags": [f"service:{h['service']}", f"severity:{h['severity']}", "type:postmortem"],
            })
        self.memory.retain_many(items, background=True)
        return len(items)

    def reset(self) -> None:
        self.memory.reset()
        self.store.clear()

    def stats(self) -> dict[str, Any]:
        incs = self.store.all()
        live = [i for i in incs if i.get("source") == "live" and i["status"] == "resolved"]
        graded = [i for i in live if (i.get("resolution") or {}).get("verdict")]
        score = {"correct": 1.0, "partial": 0.5, "wrong": 0.0}
        try:
            mem_count = self.memory.count()
        except Exception as e:
            log.warning("count failed: %s", e)
            mem_count = None
        return {
            "memory_count": mem_count,
            "incidents": len(incs), "open": sum(1 for i in incs if i["status"] == "open"),
            "accuracy": round(100 * sum(score[i["resolution"]["verdict"]] for i in graded) / len(graded)) if graded else None,
            "graded": len(graded),
            "timeline": [{
                "id": i["id"], "service": i["service"], "ttr": i.get("ttr_minutes"), "source": i.get("source"),
                "started_at": i["started_at"], "verdict": (i.get("resolution") or {}).get("verdict"),
                "confidence": ((i.get("triage") or {}).get("deja") or {}).get("confidence"),
            } for i in incs if i["status"] == "resolved"],
        }

    # ------------------------------------------------------------------ internals

    def _safe_recall(self, query: str, tags: list[str] | None = None) -> list[Memory]:
        try:
            return self.memory.recall(query, tags=tags, limit=12)
        except Exception as e:
            log.warning("recall failed: %s", e)
            return []

    def _safe_directives(self) -> list[dict[str, Any]]:
        try:
            return self.memory.directives()
        except Exception as e:
            log.warning("directives failed: %s", e)
            return []

    @staticmethod
    def _format_memories(mems: list[Memory]) -> str:
        lines = []
        for m in mems[:10]:
            when = (m.occurred_at or "")[:10]
            lines.append(f"- [{m.type}{' ' + when if when else ''}] {m.text}")
        return "\n".join(lines) or "(no memories)"

    def _incident_context(self, inc: dict[str, Any]) -> str:
        return (f"NOW: {inc['started_at']}\nSERVICE: {inc['service']} ({inc['severity']})\nALERT: {inc['alert']}\n"
                f"LOGS:\n" + "\n".join(inc.get("logs") or ["(none)"]))

    def _deja_answer(self, inc, memories, rules) -> tuple[dict[str, Any], str]:
        rules_txt = "\n".join(f"- {r['name']}: {r['content']}" for r in rules) or "(none)"
        user = (f"TEAM RULES:\n{rules_txt}\n\nMEMORIES (from Hindsight recall):\n{self._format_memories(memories)}\n\n"
                f"INCIDENT:\n{self._incident_context(inc)}")
        out = self.llm.json(DEJA_SYSTEM, user)
        if out:
            return self._normalise(out), "hindsight-recall+llm"
        try:  # no LLM key: let Hindsight reason over its own memory with a structured schema
            r = self.memory.reflect(
                "Triage this production alert using the team's incident history. " + self._incident_context(inc),
                response_schema=TRIAGE_SCHEMA)
            if r and r.structured:
                return self._normalise(r.structured), "hindsight-reflect"
        except Exception as e:
            log.warning("structured reflect failed: %s", e)
        return self._normalise(self._heuristic(inc, memories, rules)), "memory-heuristic"

    def _baseline_answer(self, inc) -> dict[str, Any]:
        out = self.llm.json(BASELINE_SYSTEM, self._incident_context(inc), temperature=0.3)
        if out:
            out["seen_before"], out["matched_incidents"] = False, []
            return self._normalise(out)
        return self._normalise(_generic_playbook(inc))

    @staticmethod
    def _normalise(d: dict[str, Any]) -> dict[str, Any]:
        def items(v):
            out = []
            for x in (v or [])[:4]:
                if isinstance(x, str):
                    x = {"action": x, "why": "", "evidence": ""}
                if isinstance(x, dict) and x.get("action"):
                    out.append({"action": str(x.get("action")), "why": str(x.get("why") or ""),
                                "evidence": str(x.get("evidence") or "")})
            return out
        try:
            conf = int(float(d.get("confidence") or 0))
        except (TypeError, ValueError):
            conf = 0
        if 0 < conf <= 1:
            conf *= 100
        page = d.get("page") if isinstance(d.get("page"), dict) else {}
        return {
            "headline": str(d.get("headline") or ""),
            "seen_before": bool(d.get("seen_before")),
            "matched_incidents": [m for m in (d.get("matched_incidents") or []) if isinstance(m, dict)][:4],
            "likely_root_cause": str(d.get("likely_root_cause") or "Unknown"),
            "confidence": max(0, min(100, conf)),
            "first_actions": items(d.get("first_actions")),
            "do_not": items(d.get("do_not")),
            "page": {"who": str(page.get("who") or "on-call lead"), "why": str(page.get("why") or "")},
        }

    @staticmethod
    def _heuristic(inc, memories, rules) -> dict[str, Any]:
        """Deterministic answer straight from postmortem metadata (no LLM available)."""
        seen: dict[str, Memory] = {}
        for m in memories:
            iid = _incident_id_of(m)
            same_service = m.metadata.get("service") == inc["service"]
            if iid and m.metadata.get("root_cause") and iid not in seen and (m.score >= 0.6 or (same_service and m.score >= 0.35)):
                seen[iid] = m
        matches = sorted(seen.values(), key=lambda m: (m.metadata.get("service") == inc["service"], m.score), reverse=True)
        do_not = [{"action": r["name"], "why": r["content"], "evidence": "team rule"} for r in rules]
        if not matches:
            g = _generic_playbook(inc)
            g.update(headline="No similar incident in memory. This looks new, so I'm using general practice.",
                     do_not=do_not + g["do_not"])
            return g
        top = matches[0].metadata
        for m in matches[:3]:
            for f in filter(None, m.metadata.get("failed", "").split(" | ")):
                do_not.append({"action": f.split(":")[0], "why": f, "evidence": m.metadata.get("incident_id", "")})
        fastest = min(matches, key=lambda m: int(m.metadata.get("ttr_minutes") or 9999)).metadata
        same = inc["service"] == top.get("service")
        return {
            "headline": f"Looks like {top.get('incident_id')} ({top.get('title')}).",
            "seen_before": True,
            "matched_incidents": [{"id": m.metadata.get("incident_id"), "date": (m.occurred_at or "")[:10],
                                   "why": m.metadata.get("title", "")} for m in matches[:3]],
            "likely_root_cause": top.get("root_cause", ""),
            "confidence": min(90, int(55 + 30 * matches[0].score + (5 if same else -15) + 5 * (len(matches) - 1))),
            "first_actions": [{"action": top.get("fix", ""), "why": "This fixed it last time",
                               "evidence": top.get("incident_id", "")}],
            "do_not": do_not,
            "page": {"who": fastest.get("resolved_by", "on-call lead"),
                     "why": f"Resolved {fastest.get('incident_id')} in {fastest.get('ttr_minutes')} min"},
        }

    @staticmethod
    def _runbook_from_metadata(service: str, mems: list[Memory]) -> str:
        rows = {}
        for m in mems:
            if m.metadata.get("service") == service and m.metadata.get("incident_id"):
                rows[m.metadata["incident_id"]] = m.metadata
        if not rows:
            return f"_No incidents remembered for {service} yet._"
        out = [f"# Runbook: {service}", "", "## Known failure modes"]
        for iid, md in sorted(rows.items()):
            out.append(f"- **{md.get('title')}** ({iid}): {md.get('root_cause')}\n  - Fix: {md.get('fix')}")
        bad = [(iid, f) for iid, md in rows.items() for f in md.get("failed", "").split(" | ") if f]
        if bad:
            out += ["", "## What NOT to do"] + [f"- {f} ({iid})" for iid, f in bad]
        return "\n".join(out)


def _generic_playbook(inc: dict[str, Any]) -> dict[str, Any]:
    text = (inc["alert"] + " " + " ".join(inc.get("logs") or [])).lower()
    if any(k in text for k in ("pool", "connection", "jdbc")):
        acts = ["Increase the DB connection pool size", "Rolling-restart the affected pods",
                "Check recent deploys and roll back if needed", "Consider failing over the database"]
    elif any(k in text for k in ("oom", "memory", "137")):
        acts = ["Increase the container memory limit", "Restart the pods", "Profile for a memory leak"]
    elif any(k in text for k in ("timeout", "dns", "lookup")):
        acts = ["Scale out the service", "Check the downstream provider's status page", "Increase client timeouts"]
    elif any(k in text for k in ("disk", "space")):
        acts = ["Expand the volume", "Delete old logs and temp files", "Restart the database"]
    else:
        acts = ["Check recent deploys", "Restart the affected pods", "Scale out"]
    return {
        "headline": "Generic triage based on the alert text alone.", "seen_before": False, "matched_incidents": [],
        "likely_root_cause": "Unknown. Could be load, a bad deploy, or a downstream dependency.", "confidence": 25,
        "first_actions": [{"action": a, "why": "Common first step", "evidence": "general practice"} for a in acts],
        "do_not": [], "page": {"who": "on-call lead", "why": "Default escalation"},
    }
