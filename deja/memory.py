"""Memory layer.

`HindsightMemory` is the real thing: every postmortem, alert and engineer
correction is retained into a Hindsight memory bank, and triage is driven by
Hindsight recall / reflect / mental models / directives.

`LocalMemory` is a small keyword-scored JSON store used only when HINDSIGHT_URL
is not configured, so the UI and tests still run offline. The UI shows a badge
whenever the fallback is active.
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from .config import Settings

log = logging.getLogger("deja.memory")

BANK_MISSION = (
    "You are the institutional memory of a platform on-call team. You remember every "
    "production incident: symptoms, root causes, the fix that worked, the attempts that "
    "did NOT work, who resolved it and how long it took. Your job is to help the next "
    "on-call engineer recognise a repeat incident fast and avoid repeating past mistakes."
)
RETAIN_MISSION = (
    "Extract operational facts: service names, symptoms and error signatures, root causes, "
    "remediation steps that worked, remediation steps that failed or made things worse, "
    "people involved, time-to-resolve, recurring schedules (cron jobs, deploys), and "
    "explicit team rules. Ignore pleasantries."
)
REFLECT_MISSION = (
    "Answer as a senior SRE who has been on this team for years. Be concrete: cite incident "
    "IDs and dates, name the fix that worked, and warn loudly about actions that failed before."
)


@dataclass
class Memory:
    id: str
    text: str
    type: str = "world"
    score: float = 0.0
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    occurred_at: str | None = None
    document_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Reflection:
    text: str
    structured: dict[str, Any] | None = None
    based_on: list[Memory] = field(default_factory=list)


class MemoryBackend(Protocol):
    name: str

    def ensure_bank(self) -> None: ...
    def retain(self, content: str, *, context: str, tags: list[str], timestamp: str,
               document_id: str | None = None, metadata: dict[str, str] | None = None) -> None: ...
    def retain_many(self, items: list[dict[str, Any]], *, background: bool = False) -> None: ...
    def recall(self, query: str, *, tags: list[str] | None = None, limit: int = 12) -> list[Memory]: ...
    def reflect(self, query: str, *, tags: list[str] | None = None, context: str | None = None,
                response_schema: dict[str, Any] | None = None) -> Reflection | None: ...
    def runbook(self, service: str) -> str | None: ...
    def refresh_runbook(self, service: str) -> None: ...
    def add_directive(self, name: str, content: str) -> None: ...
    def directives(self) -> list[dict[str, Any]]: ...
    def recent(self, limit: int = 40) -> list[Memory]: ...
    def count(self) -> int: ...
    def reset(self) -> None: ...


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "general"


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    return v.isoformat() if isinstance(v, datetime) else str(v)


# --------------------------------------------------------------------------- Hindsight


class HindsightMemory:
    name = "hindsight"

    def __init__(self, s: Settings):
        from hindsight_client import Hindsight

        self.client = Hindsight(base_url=s.hindsight_url, api_key=s.hindsight_api_key or None, timeout=180)
        self.bank = s.bank_id
        self._ready = False
        self._lock = threading.Lock()

    def ensure_bank(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            # create_bank is an upsert (PUT), so this is safe on every boot.
            self.client.create_bank(
                bank_id=self.bank,
                name="Deja on-call memory",
                mission=BANK_MISSION,
                retain_mission=RETAIN_MISSION,
                reflect_mission=REFLECT_MISSION,
                disposition_skepticism=4,
                disposition_literalism=4,
                disposition_empathy=2,
            )
            self._ready = True

    def retain(self, content, *, context, tags, timestamp, document_id=None, metadata=None):
        self.retain_many([{
            "content": content, "context": context, "tags": tags, "timestamp": timestamp,
            "document_id": document_id, "metadata": metadata or {},
        }])

    def retain_many(self, items, *, background=False):
        self.ensure_bank()
        clean = []
        for it in items:
            item = {k: v for k, v in it.items() if v not in (None, "", [], {})}
            if "metadata" in item:  # Hindsight metadata is dict[str, str]
                item["metadata"] = {k: str(v) for k, v in item["metadata"].items()}
            clean.append(item)
        # Batches of 8 keep individual requests well under server limits.
        for i in range(0, len(clean), 8):
            self.client.retain_batch(bank_id=self.bank, items=clean[i:i + 8], retain_async=background)

    def recall(self, query, *, tags=None, limit=12):
        self.ensure_bank()
        resp = self.client.recall(
            bank_id=self.bank, query=query, budget="mid", max_tokens=4096,
            tags=tags, tags_match="any",
        )
        out: list[Memory] = []
        n = len(resp.results or [])
        for rank, r in enumerate(resp.results or []):
            out.append(Memory(
                id=r.id, text=r.text, type=r.type or "world",
                score=round(1.0 - rank / max(n, 1), 3),  # results arrive already fused + reranked
                tags=list(r.tags or []), metadata=dict(r.metadata or {}),
                occurred_at=_iso(r.occurred_start or r.mentioned_at), document_id=r.document_id,
            ))
        return out[:limit]

    def reflect(self, query, *, tags=None, context=None, response_schema=None):
        self.ensure_bank()
        resp = self.client.reflect(
            bank_id=self.bank, query=query, budget="mid", context=context, tags=tags,
            response_schema=response_schema, include_facts=True,
        )
        based: list[Memory] = []
        if resp.based_on and resp.based_on.memories:
            for f in resp.based_on.memories:
                based.append(Memory(id=getattr(f, "id", "") or "", text=getattr(f, "text", ""),
                                    type=getattr(f, "type", None) or "world"))
        structured = resp.structured_output if isinstance(resp.structured_output, dict) else None
        return Reflection(text=resp.text or "", structured=structured, based_on=based)

    def _mm_id(self, service: str) -> str:
        return f"runbook-{_slug(service)}"

    def runbook(self, service):
        """The per-service runbook is a Hindsight *mental model*: a living document
        that Hindsight re-derives from the bank after each consolidation."""
        self.ensure_bank()
        mm_id = self._mm_id(service)
        try:
            mm = self.client.get_mental_model(bank_id=self.bank, mental_model_id=mm_id, detail="content")
            return mm.content or None
        except Exception:
            try:
                self.client.create_mental_model(
                    bank_id=self.bank, id=mm_id, name=f"Runbook: {service}",
                    source_query=(
                        f"Write the operational runbook for {service}: known failure modes with their "
                        f"symptoms, the fix that worked for each (with incident IDs), actions that did NOT "
                        f"work or made things worse, recurring triggers and schedules, and who to page."
                    ),
                    tags=[f"service:{service}"],
                    trigger={"refresh_after_consolidation": True},
                )
            except Exception as e:  # already exists / racing create
                log.info("create_mental_model(%s): %s", mm_id, e)
            return None

    def refresh_runbook(self, service):
        try:
            self.client.refresh_mental_model(bank_id=self.bank, mental_model_id=self._mm_id(service))
        except Exception as e:
            log.info("refresh_mental_model(%s): %s", service, e)

    def add_directive(self, name, content):
        self.ensure_bank()
        self.client.create_directive(bank_id=self.bank, name=name, content=content, priority=10)

    def directives(self):
        self.ensure_bank()
        resp = self.client.list_directives(bank_id=self.bank)
        items = getattr(resp, "items", None) or getattr(resp, "directives", None) or resp or []
        return [{"name": d.name, "content": d.content, "created_at": _iso(getattr(d, "created_at", None))}
                for d in items]

    def recent(self, limit=40):
        self.ensure_bank()
        resp = self.client.list_memories(bank_id=self.bank, limit=limit)
        return [Memory(id=m.id, text=m.text, type=m.fact_type or "world", tags=list(m.tags or []),
                       metadata=dict(m.metadata or {}), occurred_at=_iso(m.occurred_start or m.mentioned_at),
                       document_id=m.document_id)
                for m in resp.items or []]

    def count(self):
        self.ensure_bank()
        return int(self.client.list_memories(bank_id=self.bank, limit=1).total or 0)

    def reset(self):
        try:
            self.client.delete_bank(bank_id=self.bank)
        except Exception as e:
            log.info("delete_bank: %s", e)
        self._ready = False
        self.ensure_bank()


# --------------------------------------------------------------------------- Local fallback

_STOP = set("""a an the and or of to in on for with at by from is was were be been it this that
as are not no but if then than into after before over under we our you they he she them its via
""".split())


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9][a-z0-9_.\-]+", text.lower()) if t not in _STOP and len(t) > 2]


class LocalMemory:
    """Offline stand-in. BM25 over whole documents, with tag + recency boosts."""

    name = "local"

    def __init__(self, s: Settings):
        self.path = s.data_dir / "local_memory.json"
        self._lock = threading.Lock()
        self._items: list[dict[str, Any]] = []
        self._directives: list[dict[str, Any]] = []
        if self.path.exists():
            raw = json.loads(self.path.read_text())
            self._items, self._directives = raw.get("items", []), raw.get("directives", [])

    def _save(self):
        self.path.write_text(json.dumps({"items": self._items, "directives": self._directives}, indent=1))

    def ensure_bank(self):
        pass

    def retain(self, content, *, context, tags, timestamp, document_id=None, metadata=None):
        self.retain_many([{"content": content, "context": context, "tags": tags, "timestamp": timestamp,
                           "document_id": document_id, "metadata": metadata or {}}])

    def retain_many(self, items, *, background=False):
        with self._lock:
            for it in items:
                self._items.append({
                    "id": uuid.uuid4().hex[:12], "text": it["content"], "context": it.get("context", ""),
                    "tags": it.get("tags") or [], "metadata": {k: str(v) for k, v in (it.get("metadata") or {}).items()},
                    "occurred_at": it.get("timestamp"), "document_id": it.get("document_id"),
                    "type": "experience" if it.get("context") == "engineer feedback" else "world",
                })
            self._save()

    def recall(self, query, *, tags=None, limit=12):
        with self._lock:
            docs = list(self._items)
        if not docs:
            return []
        q = Counter(_tokens(query))
        toks = [_tokens(d["text"] + " " + " ".join(d["metadata"].values())) for d in docs]
        avg = sum(map(len, toks)) / len(toks)
        df = Counter(t for ts in toks for t in set(ts))
        scored = []
        for d, ts in zip(docs, toks):
            tf = Counter(ts)
            s = 0.0
            for term in q:
                if term in tf:
                    idf = math.log(1 + (len(docs) - df[term] + 0.5) / (df[term] + 0.5))
                    s += idf * tf[term] * 2.2 / (tf[term] + 1.2 * (0.25 + 0.75 * len(ts) / avg))
            if tags and set(tags) & set(d["tags"]):
                s *= 1.5
            if s > 0:
                scored.append((s, d))
        scored.sort(key=lambda x: (x[0], x[1].get("occurred_at") or ""), reverse=True)
        top = scored[0][0] if scored else 1
        return [Memory(id=d["id"], text=d["text"], type=d["type"], score=round(s / top, 3), tags=d["tags"],
                       metadata=d["metadata"], occurred_at=d["occurred_at"], document_id=d["document_id"])
                for s, d in scored[:limit]]

    def reflect(self, query, *, tags=None, context=None, response_schema=None):
        return None  # caller composes an answer from recall() with the LLM instead

    def runbook(self, service):
        return None

    def refresh_runbook(self, service):
        pass

    def add_directive(self, name, content):
        with self._lock:
            self._directives.append({"name": name, "content": content,
                                     "created_at": datetime.now(timezone.utc).isoformat()})
            self._save()

    def directives(self):
        return list(self._directives)

    def recent(self, limit=40):
        items = sorted(self._items, key=lambda d: d.get("occurred_at") or "", reverse=True)[:limit]
        return [Memory(id=d["id"], text=d["text"], type=d["type"], tags=d["tags"], metadata=d["metadata"],
                       occurred_at=d["occurred_at"], document_id=d["document_id"]) for d in items]

    def count(self):
        return len(self._items)

    def reset(self):
        with self._lock:
            self._items, self._directives = [], []
            self._save()


def build_memory(s: Settings) -> MemoryBackend:
    if s.hindsight_enabled:
        return HindsightMemory(s)
    log.warning("HINDSIGHT_URL not set - using LocalMemory fallback (demo only)")
    return LocalMemory(s)
