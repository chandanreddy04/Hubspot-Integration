#!/usr/bin/env python3
"""End-to-end test runner: HubSpot closed deal -> agreement PDF -> text ->
useful structured content.

Zero credentials needed to run this as-is — it reads a local sample
agreement instead of calling HubSpot, and extracts fields with a regex
heuristic instead of calling an LLM. Flip HUBSPOT_MODE=live / LLM_MODE=live
in .env once you have real credentials; nothing else about this script
changes.

Usage:
    python run_pipeline.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # avoid mangled output on Windows consoles

from config import load_settings
from content_extractor import extract
from pdf_text import extract_text

FIXTURE = Path(__file__).parent / "fixtures" / "sample_agreement.txt"


def _step(n: int, title: str) -> None:
    print(f"\n[{n}] {title}")
    print("-" * (len(title) + 5))


_FIELD_LABELS = {
    "customer_legal_name": "Customer",
    "billing_email": "Billing email",
    "effective_date": "Effective date",
    "payment_terms": "Payment terms",
    "stated_total": "Total",
    "signed": "Signed",
}
LOW_CONFIDENCE = 0.8


def _format_value(key: str, value, fields: dict) -> str:
    if value is None:
        return "(not found)"
    if key == "stated_total":
        currency = fields.get("currency") or "USD"
        return f"{value:,.2f} {currency}"
    if key == "signed":
        return "Yes" if value else "No"
    return str(value)


def _print_result(result) -> None:
    fields, confidence = result.fields, result.confidence
    rows = []
    for key, label in _FIELD_LABELS.items():
        if key not in fields:
            continue
        value = _format_value(key, fields.get(key), fields)
        conf = confidence.get(key)
        conf_str = f"{conf * 100:.0f}%" if conf is not None else "—"
        flag = "  <- needs review" if conf is not None and conf < LOW_CONFIDENCE else ""
        rows.append((label, value, conf_str, flag))

    label_w = max(len(r[0]) for r in rows)
    value_w = max(len(r[1]) for r in rows)
    for label, value, conf_str, flag in rows:
        print(f"  {label:<{label_w}}   {value:<{value_w}}   {conf_str:>4}{flag}")

    low = [_FIELD_LABELS[k] for k, c in confidence.items() if c < LOW_CONFIDENCE and k in _FIELD_LABELS]
    if low:
        print(f"\n  {len(low)} field(s) below {int(LOW_CONFIDENCE * 100)}% confidence — worth a human look: {', '.join(low)}")
    else:
        print(f"\n  All fields at or above {int(LOW_CONFIDENCE * 100)}% confidence.")


def get_agreements(settings) -> list[tuple[bytes, str]]:
    """Returns [(raw_bytes, source_description), ...]. Live path pulls every
    closed deal's signed agreement from HubSpot, not just the first one
    found; mock path returns the single local fixture so this script still
    runs with zero credentials."""
    if settings.hubspot_mode == "live":
        from integrations.hubspot import HubSpotClient, NoAgreementFound

        client = HubSpotClient(settings.hubspot_token)
        out = []
        for deal in client.list_closed_deals(limit=10):
            if not deal.agreement_ref:
                continue
            try:
                agreement = client.get_agreement(deal.agreement_ref)
            except NoAgreementFound:
                continue
            desc = f"HubSpot deal {deal.deal_id} ({deal.deal_name}) — {agreement.filename}"
            out.append((agreement.content, desc))
        if not out:
            raise RuntimeError("no closed deal with a resolvable signed agreement was found")
        return out

    return [(FIXTURE.read_bytes(), f"local fixture — {FIXTURE.name} (HUBSPOT_MODE=mock)")]


def process_agreement(raw_bytes: bytes, source: str, settings) -> dict | None:
    """Runs steps 2-4 for one agreement. Returns the result's fields dict
    for the closing summary, or None if it needed OCR and was skipped."""
    print(f"  source: {source}")
    print(f"  size:   {len(raw_bytes):,} bytes")

    _step(2, "Extract text from the agreement")
    parsed = extract_text(raw_bytes)
    print(f"  method:    {parsed.method}")
    print(f"  needs_ocr: {parsed.needs_ocr}")
    print(f"  chars:     {len(parsed.text):,}")
    if parsed.needs_ocr:
        print("  -> no usable text; this document would route to a human. Skipping.")
        return None

    _step(3, "Extract useful content from the text")
    result = extract(parsed.text, settings)
    print(f"  method: {result.method}")

    _step(4, "Result")
    _print_result(result)

    print("\n  Raw JSON:")
    for line in json.dumps({"fields": result.fields, "confidence": result.confidence}, indent=2).splitlines():
        print(f"  {line}")

    return result.fields


def main() -> None:
    settings = load_settings()

    print("Agreement extraction pipeline")
    print(f"  HUBSPOT_MODE = {settings.hubspot_mode}")
    print(f"  LLM_MODE     = {settings.llm_mode}")
    if settings.llm_mode == "live":
        print(f"  LLM_PROVIDER = {settings.llm_provider}")

    _step(1, "Resolve every closed deal's signed agreement")
    agreements = get_agreements(settings)
    print(f"  found {len(agreements)} closed deal(s) with a resolvable agreement")

    summary: list[tuple[str, dict]] = []
    for i, (raw_bytes, source) in enumerate(agreements, start=1):
        print(f"\n{'=' * 60}")
        print(f"Deal {i} of {len(agreements)}")
        print("=" * 60)
        fields = process_agreement(raw_bytes, source, settings)
        if fields is not None:
            summary.append((source, fields))

    if len(agreements) > 1:
        print(f"\n{'=' * 60}")
        print("Summary — all deals")
        print("=" * 60)
        for source, fields in summary:
            total = fields.get("stated_total")
            total_str = f"{total:,.2f} {fields.get('currency') or 'USD'}" if total is not None else "(not found)"
            print(f"  {fields.get('customer_legal_name') or '(unknown)':<30}  {total_str:<18}  {source}")


if __name__ == "__main__":
    main()
