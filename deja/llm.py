"""Thin Groq wrapper that only ever returns parsed JSON or None.

Open-weight models occasionally emit malformed JSON or reject JSON mode, so
every call: tries the primary model in JSON mode, repairs obvious wrappers
(```json fences, <think> blocks), retries once on the fallback model, and
finally returns None so callers can degrade to a deterministic answer.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from .config import Settings

log = logging.getLogger("deja.llm")


def extract_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        v = json.loads(text)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            v = json.loads(text[start:end + 1])
            return v if isinstance(v, dict) else None
        except json.JSONDecodeError:
            return None
    return None


class LLM:
    def __init__(self, s: Settings):
        self.enabled = s.llm_enabled
        self.models = [s.groq_model, s.groq_fallback_model]
        self.client = None
        if self.enabled:
            from groq import Groq

            self.client = Groq(api_key=s.groq_api_key, timeout=45, max_retries=1)

    def json(self, system: str, user: str, *, temperature: float = 0.2, max_tokens: int = 1800) -> dict[str, Any] | None:
        if not self.client:
            return None
        for model in self.models:
            for json_mode in (True, False):
                try:
                    kwargs: dict[str, Any] = dict(
                        model=model, temperature=temperature, max_tokens=max_tokens,
                        messages=[{"role": "system", "content": system + "\nRespond with a single JSON object only."},
                                  {"role": "user", "content": user}],
                    )
                    if json_mode:
                        kwargs["response_format"] = {"type": "json_object"}
                    resp = self.client.chat.completions.create(**kwargs)
                    parsed = extract_json(resp.choices[0].message.content or "")
                    if parsed is not None:
                        return parsed
                    log.warning("%s returned unparseable JSON (json_mode=%s)", model, json_mode)
                except Exception as e:  # rate limits, json_validate_failed, tool errors...
                    log.warning("%s failed (json_mode=%s): %s", model, json_mode, str(e)[:200])
        return None

    def text(self, system: str, user: str, *, temperature: float = 0.3, max_tokens: int = 900) -> str | None:
        if not self.client:
            return None
        for model in self.models:
            try:
                resp = self.client.chat.completions.create(
                    model=model, temperature=temperature, max_tokens=max_tokens,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                )
                out = re.sub(r"<think>.*?</think>", "", resp.choices[0].message.content or "", flags=re.S).strip()
                if out:
                    return out
            except Exception as e:
                log.warning("%s text call failed: %s", model, str(e)[:200])
        return None
