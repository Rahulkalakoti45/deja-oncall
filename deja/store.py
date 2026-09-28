"""Tiny JSON-file incident store (the system of record for the UI timeline).
Knowledge lives in Hindsight; this only tracks incident lifecycle and metrics."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class IncidentStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, Any]] = {}
        if path.exists():
            self._data = {i["id"]: i for i in json.loads(path.read_text())}

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(list(self._data.values()), indent=1))
        tmp.replace(self.path)

    def upsert(self, inc: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._data[inc["id"]] = inc
            self._save()
        return inc

    def get(self, inc_id: str) -> dict[str, Any] | None:
        return self._data.get(inc_id)

    def all(self) -> list[dict[str, Any]]:
        return sorted(self._data.values(), key=lambda i: i["started_at"])

    def next_id(self) -> str:
        nums = [int(k.split("-")[1]) for k in self._data if k.startswith("INC-") and k.split("-")[1].isdigit()]
        return f"INC-{max(nums, default=2140) + 1}"

    def clear(self) -> None:
        with self._lock:
            self._data = {}
            self._save()
