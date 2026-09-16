"""Settings for the coworker platform. stdlib only, same .env pattern as
rally-ar-agent's config.py — reads a local .env once, then os.environ."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).parent


def _load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.split(" #", 1)[0].strip()
        os.environ.setdefault(key, value)


def _env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name)
    return val if val not in (None, "") else default


@dataclass
class Settings:
    hubspot_token: str | None = None
    hubspot_mode: str = "mock"  # "mock" | "live" — mock is the safe default
    llm_mode: str = "mock"      # "mock" | "live" — mock is the safe default
    llm_provider: str = "anthropic"  # "anthropic" | "groq" — which live client to use
    anthropic_api_key: str | None = None
    llm_model: str = "claude-sonnet-5"
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-20b"


def load_settings() -> Settings:
    _load_dotenv()
    token = _env("HUBSPOT_TOKEN")
    # live only if explicitly requested AND a token is present — never fall
    # into a real API call just because a token happens to be set.
    mode = (_env("HUBSPOT_MODE", "mock") or "mock").lower()
    if mode == "live" and not token:
        raise RuntimeError("HUBSPOT_MODE=live but HUBSPOT_TOKEN is not set")

    llm_mode = (_env("LLM_MODE", "mock") or "mock").lower()
    provider = (_env("LLM_PROVIDER", "anthropic") or "anthropic").lower()
    if provider not in ("anthropic", "groq"):
        raise RuntimeError(f"LLM_PROVIDER must be 'anthropic' or 'groq', got {provider!r}")

    anthropic_key = _env("ANTHROPIC_API_KEY")
    groq_key = _env("GROQ_API_KEY")
    if llm_mode == "live":
        if provider == "anthropic" and not anthropic_key:
            raise RuntimeError("LLM_MODE=live with LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set")
        if provider == "groq" and not groq_key:
            raise RuntimeError("LLM_MODE=live with LLM_PROVIDER=groq but GROQ_API_KEY is not set")

    return Settings(
        hubspot_token=token, hubspot_mode=mode,
        llm_mode=llm_mode, llm_provider=provider,
        anthropic_api_key=anthropic_key, llm_model=_env("LLM_MODEL", "claude-sonnet-5"),
        groq_api_key=groq_key, groq_model=_env("GROQ_MODEL", "openai/gpt-oss-20b"),
    )
