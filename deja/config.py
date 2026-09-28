"""Runtime configuration, loaded once from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    hindsight_url: str = os.getenv("HINDSIGHT_URL", "").rstrip("/")
    hindsight_api_key: str = os.getenv("HINDSIGHT_API_KEY", "")
    bank_id: str = os.getenv("DEJA_BANK_ID", "deja-oncall")
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    groq_fallback_model: str = os.getenv("GROQ_FALLBACK_MODEL", "qwen/qwen3-32b")
    data_dir: Path = Path(os.getenv("DEJA_DATA_DIR", str(ROOT / "data")))

    @property
    def hindsight_enabled(self) -> bool:
        return bool(self.hindsight_url)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.groq_api_key)


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
