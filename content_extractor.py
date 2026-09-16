"""Turn agreement text into the useful fields an AR team actually needs.

Two modes, chosen by config.Settings.llm_mode (independent of HUBSPOT_MODE —
you can pull real contracts from HubSpot while still using the free-text
fallback here, or vice versa):

  - "live": one call to the real Anthropic Messages API (needs
    ANTHROPIC_API_KEY). Same prompt/schema shape as rally-ar-agent's
    integrations/llm_live.py.
  - "mock": no API call — a handful of regex heuristics over the raw text.
    Good enough to prove the pipeline end-to-end with zero credentials;
    not a substitute for the LLM step on real, varied contract language.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from config import Settings
from integrations import _http

_SCHEMA_HINT = {
    "customer_legal_name": "string",
    "billing_email": "string",
    "effective_date": "YYYY-MM-DD",
    "payment_terms": "e.g. 'Net 30'",
    "currency": "ISO code, e.g. USD",
    "stated_total": "number — the total contract value stated in the document",
    "signed": "boolean — is the agreement signed by both parties",
}

_PROMPT = (
    "You extract billing terms from a signed services agreement so an invoice can be raised.\n"
    "Return ONLY a JSON object with these keys (no prose):\n"
    f"{json.dumps(_SCHEMA_HINT, indent=2)}\n"
    "Also return a parallel object 'confidence' mapping each key to a 0..1 number.\n"
    "Wrap the whole thing as {\"fields\": {...}, \"confidence\": {...}}.\n\n"
    "AGREEMENT:\n"
)


@dataclass
class ExtractionResult:
    fields: dict = field(default_factory=dict)
    confidence: dict[str, float] = field(default_factory=dict)
    method: str = "unknown"  # "llm" | "heuristic"


def extract_anthropic(agreement_text: str, settings: Settings) -> ExtractionResult:
    res = _http.request(
        "POST",
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": settings.anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json_body={
            "model": settings.llm_model,
            "max_tokens": 1200,
            "messages": [{"role": "user", "content": _PROMPT + agreement_text}],
        },
    )
    text = "".join(block.get("text", "") for block in res.get("content", []))
    parsed = _extract_json(text)
    return ExtractionResult(
        fields=parsed.get("fields", {}),
        confidence={k: float(v) for k, v in parsed.get("confidence", {}).items()},
        method="llm (anthropic)",
    )


def extract_groq(agreement_text: str, settings: Settings) -> ExtractionResult:
    """Groq's chat completions endpoint is OpenAI-compatible — same prompt,
    different wire format and response shape. Useful as a free-tier live
    test path when Anthropic credits aren't available yet."""
    res = _http.request(
        "POST",
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {settings.groq_api_key}",
            "content-type": "application/json",
        },
        json_body={
            "model": settings.groq_model,
            "max_tokens": 1200,
            "messages": [{"role": "user", "content": _PROMPT + agreement_text}],
        },
    )
    text = res["choices"][0]["message"]["content"]
    parsed = _extract_json(text)
    return ExtractionResult(
        fields=parsed.get("fields", {}),
        confidence={k: float(v) for k, v in parsed.get("confidence", {}).items()},
        method="llm (groq)",
    )


def _extract_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise RuntimeError(f"LLM did not return JSON: {text[:300]}")
    return json.loads(text[start : end + 1])


# -- heuristic fallback, no API key needed -------------------------------- #
_DATE_RE = re.compile(
    r"\b(?:effective(?:\s+as of)?\s+date[:\s]*)?"
    r"((?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)
_TERMS_RE = re.compile(r"\bNet\s?(\d{1,3})\b", re.IGNORECASE)
_AMOUNT_RE = re.compile(r"\$\s?([\d,]+(?:\.\d{2})?)")
_CUSTOMER_RE = re.compile(r"(?:Customer|Client)\s*:\s*([A-Z][\w&.,'\- ]{2,60})")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_SIGNED_HINTS = ("signature", "signed by", "/s/", "docusign", "duly executed")


def _parse_date(raw: str) -> str | None:
    from datetime import datetime

    for fmt in ("%B %d, %Y", "%B %d %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip().rstrip(","), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def extract_heuristic(agreement_text: str) -> ExtractionResult:
    fields: dict = {}
    conf: dict[str, float] = {}

    if m := _CUSTOMER_RE.search(agreement_text):
        fields["customer_legal_name"] = m.group(1).strip()
        conf["customer_legal_name"] = 0.7
    else:
        fields["customer_legal_name"] = None
        conf["customer_legal_name"] = 0.0

    if m := _EMAIL_RE.search(agreement_text):
        fields["billing_email"] = m.group(0)
        conf["billing_email"] = 0.6
    else:
        fields["billing_email"] = None
        conf["billing_email"] = 0.0

    if m := _DATE_RE.search(agreement_text):
        parsed = _parse_date(m.group(1))
        fields["effective_date"] = parsed
        conf["effective_date"] = 0.65 if parsed else 0.2
    else:
        fields["effective_date"] = None
        conf["effective_date"] = 0.0

    if m := _TERMS_RE.search(agreement_text):
        fields["payment_terms"] = f"Net {m.group(1)}"
        conf["payment_terms"] = 0.8
    else:
        fields["payment_terms"] = None
        conf["payment_terms"] = 0.0

    amounts = [float(a.replace(",", "")) for a in _AMOUNT_RE.findall(agreement_text)]
    if amounts:
        fields["stated_total"] = max(amounts)  # heuristic: the largest $ figure mentioned
        conf["stated_total"] = 0.5
    else:
        fields["stated_total"] = None
        conf["stated_total"] = 0.0
    fields["currency"] = "USD" if amounts else None
    conf["currency"] = 0.5 if amounts else 0.0

    lowered = agreement_text.lower()
    fields["signed"] = any(h in lowered for h in _SIGNED_HINTS)
    conf["signed"] = 0.6

    return ExtractionResult(fields=fields, confidence=conf, method="heuristic")


def extract(agreement_text: str, settings: Settings) -> ExtractionResult:
    if settings.llm_mode == "live":
        if settings.llm_provider == "groq":
            return extract_groq(agreement_text, settings)
        return extract_anthropic(agreement_text, settings)
    return extract_heuristic(agreement_text)
