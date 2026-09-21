#!/usr/bin/env python3
"""Serves emma_workspace.html plus a real /api/deals endpoint backed by the
actual HubSpot + LLM pipeline (same modules run_pipeline.py uses).

Only the Deal Pipeline / contract data is real here, by design — Approvals,
Cash Application, Collections, Incidents, and Audit stay as the existing
mock data in emma_workspace.html; those workflows don't exist yet.

Real deals are computed once and cached in memory (LLM calls aren't free
and this project has already hit real rate limits calling too often) --
hit /api/deals?refresh=1 to force a recompute.

Usage:
    python server.py [port]        # default port 8600
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).parent.parent  # Hubspot-Integration/ -- config.py, integrations/, etc. live here
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from config import load_settings
from content_extractor import extract
from pdf_text import extract_text

UI_DIR = Path(__file__).parent

_cache: dict = {"deals": None, "computed_at": 0.0, "error": None}
_cash_cache: dict = {"cash": None, "computed_at": 0.0, "error": None}
_CACHE_TTL_SECONDS = 300  # re-fetch at most every 5 minutes even without ?refresh=1


def _prio_for(amount: float) -> str:
    if amount >= 50_000:
        return "high"
    if amount >= 15_000:
        return "med"
    return "low"


def _fmt_signed_date(iso_date: str | None) -> str:
    if not iso_date:
        return "—"
    try:
        return datetime.fromisoformat(iso_date).strftime("%b %d, %Y")
    except ValueError:
        return iso_date


def _build_line_items(raw: list | None) -> list[dict]:
    """Reshape the extractor's {description, quantity, unit_price, amount}
    dicts into the {desc, qty, unit, amount} the UI's invoice tables expect.
    Returns [] when nothing was extracted -- the UI already renders that as
    an honest "not extracted" gap rather than a fabricated split."""
    if not raw:
        return []
    items = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        items.append({
            "desc": it.get("description") or "—",
            "qty": it.get("quantity") if it.get("quantity") is not None else 1,
            "unit": it.get("unit_price") if it.get("unit_price") is not None else 0,
            "amount": it.get("amount") if it.get("amount") is not None else 0,
        })
    return items


def _build_signatories(fields: dict) -> list[str]:
    """One entry for the customer's signatory -- only when the extractor
    actually found a name. Provider-side (Rally) signatory isn't extracted
    from the customer's contract, so it's never invented here either.

    Joined with an em dash, not a comma: the UI's edit-mode signatories
    field treats a comma as the separator between multiple different
    signatories (join(', ') / split(',') round-trip) -- a literal comma
    inside one signatory's own "Name, Title" string would get silently
    split into two fake signatories on the next save.
    """
    name = fields.get("signatory_name")
    if not name:
        return []
    title = fields.get("signatory_title")
    return [f"{name} — {title}" if title else name]


def _build_deal(deal, agreement, fields: dict, confidence: dict, page_count: int) -> dict:
    confs = [c for c in confidence.values() if c is not None]
    avg_conf = round(sum(confs) / len(confs), 2) if confs else 0.0
    total = fields.get("stated_total") or deal.amount or 0
    billing_email = deal.billing_email or fields.get("billing_email") or ""
    party_to_domain = billing_email.split("@")[-1] if "@" in billing_email else "—"

    return {
        "id": deal.deal_id,
        "c": fields.get("customer_legal_name") or deal.company_legal_name or deal.deal_name,
        "amt": total,
        "stage": "extract",  # honest: extraction is what's real; draft/approval/sent/tracking/paid aren't built
        "days": 0,
        "prio": _prio_for(total),
        "owner": "—",  # not fetched from HubSpot today -- no deal-owner API call exists yet
        "terms": fields.get("payment_terms") or "—",
        "extractedAt": datetime.now().strftime("%b %d, %H:%M"),
        "conf": avg_conf,
        "real": True,
        "lineItems": _build_line_items(fields.get("line_items")),
        "termMonths": fields.get("term_months"),
        "autoRenew": fields.get("auto_renew"),
        "agreementRef": deal.agreement_ref,  # lets the UI re-fetch the real PDF bytes on demand
        "contract": {
            "filename": agreement.filename,
            "signed_date": _fmt_signed_date(fields.get("effective_date")),
            "pages": page_count,
            "size_kb": round(len(agreement.content) / 1024),
            "party_from": "Rally, Inc.",
            "party_from_domain": "rally.example",
            "party_to": fields.get("customer_legal_name") or deal.company_legal_name,
            "party_to_domain": party_to_domain,
            "signatories": _build_signatories(fields),
            "mock_body": "",  # filled in below with real extracted text
        },
    }


def compute_deals() -> list[dict]:
    from integrations.hubspot import HubSpotClient, NoAgreementFound
    import pdfplumber
    import io

    settings = load_settings()
    if settings.hubspot_mode != "live":
        raise RuntimeError("HUBSPOT_MODE is not 'live' -- set it in .env to fetch real deals")

    client = HubSpotClient(settings.hubspot_token)
    out = []
    for deal in client.list_closed_deals(limit=10):
        if not deal.agreement_ref:
            continue
        try:
            agreement = client.get_agreement(deal.agreement_ref)
        except NoAgreementFound:
            continue

        parsed = extract_text(agreement.content)
        if parsed.needs_ocr:
            continue
        result = extract(parsed.text, settings)

        try:
            with pdfplumber.open(io.BytesIO(agreement.content)) as pdf:
                page_count = len(pdf.pages)
        except Exception:
            page_count = 1

        built = _build_deal(deal, agreement, result.fields, result.confidence, page_count)
        built["contract"]["mock_body"] = parsed.text[:4000]
        out.append(built)

    return out


def get_deals(force_refresh: bool = False) -> tuple[list[dict], str | None]:
    stale = time.time() - _cache["computed_at"] > _CACHE_TTL_SECONDS
    if force_refresh or _cache["deals"] is None or stale:
        try:
            _cache["deals"] = compute_deals()
            _cache["error"] = None
        except Exception as exc:
            _cache["error"] = str(exc)
            if _cache["deals"] is None:
                _cache["deals"] = []
        _cache["computed_at"] = time.time()
    return _cache["deals"], _cache["error"]


def _fmt_when(iso_date: str) -> str:
    try:
        return datetime.fromisoformat(iso_date).strftime("%b %d · %H:%M")
    except (ValueError, TypeError):
        return iso_date or "—"


def compute_cash() -> dict:
    """Real QuickBooks payment/invoice data for the Cash Application tab.

    No fuzzy matching is invented here -- "matched" means QuickBooks' own
    Payment.LinkedTxn already ties that payment to an invoice (an
    authoritative fact from QBO, not a guess we're making), so confidence
    is honestly 1.0. "Unmatched" means no LinkedTxn exists; since no
    matching algorithm has been built, confidence is honestly 0.0 and the
    candidate-invoice list is genuinely empty rather than fabricated.
    "Partial" is derived directly from real invoice Balance vs TotalAmt.
    """
    from integrations.qbo import QboClient

    settings = load_settings()
    if settings.qbo_mode != "live":
        raise RuntimeError("QBO_MODE is not 'live' -- set it in .env to fetch real cash data")

    client = QboClient(settings)
    payments, _ = client.list_payments_since(None)
    invoices = client.list_invoices(limit=50)

    matched, unmatched = [], []
    for p in payments:
        row = {
            "qb": p.payment_id,
            "c": p.customer_name or "(no customer name on payment)",
            "amt": round(p.total_amount, 2),
            "when": _fmt_when(p.txn_date),
            "real": True,
        }
        if p.linked_invoice_numbers:
            row["inv"] = ", ".join(p.linked_invoice_numbers)
            row["conf"] = 1.0  # QBO's own LinkedTxn -- a fact, not a fuzzy guess
            matched.append(row)
        else:
            row["inv"] = "—"
            row["conf"] = 0.0  # honestly zero: no matching algorithm exists yet
            row["cands"] = []  # not fabricated -- genuinely no suggestions to offer
            unmatched.append(row)

    partial = []
    for inv in invoices:
        total = float(inv.get("TotalAmt") or 0)
        balance = float(inv.get("Balance") or 0)
        if 0 < balance < total:
            partial.append({
                "qb": f"QBO-Inv-{inv.get('Id')}",  # an invoice reference, not a payment ID -- QBO's query API doesn't return a payment ID per partially-paid invoice
                "c": inv.get("CustomerRef", {}).get("name", "—"),
                "amt": round(total - balance, 2),
                "due": round(total, 2),
                "inv": inv.get("DocNumber", "—"),
                "conf": 1.0,  # derived directly from real Balance vs TotalAmt, not a guess
                "when": "—",  # list_invoices doesn't carry a payment date
                "real": True,
            })

    return {"matched": matched, "unmatched": unmatched, "partial": partial}


def get_cash(force_refresh: bool = False) -> tuple[dict | None, str | None]:
    stale = time.time() - _cash_cache["computed_at"] > _CACHE_TTL_SECONDS
    if force_refresh or _cash_cache["cash"] is None or stale:
        try:
            _cash_cache["cash"] = compute_cash()
            _cash_cache["error"] = None
        except Exception as exc:
            _cash_cache["error"] = str(exc)
            if _cash_cache["cash"] is None:
                _cash_cache["cash"] = {"matched": [], "unmatched": [], "partial": []}
        _cash_cache["computed_at"] = time.time()
    return _cash_cache["cash"], _cash_cache["error"]


class Handler(BaseHTTPRequestHandler):
    server_version = "EmmaUI/0.1"

    def log_message(self, fmt, *args) -> None:
        sys.stderr.write(f"{self.address_string()} - {fmt % args}\n")

    def _send_json(self, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_deal_pdf(self, deal_id: str) -> None:
        """Re-fetches the real signed PDF from HubSpot so the UI can show
        the actual document (real pages, real download) instead of a
        flattened text preview. Not cached -- this endpoint is only hit
        when a user actually opens/downloads a contract, not on every
        page load."""
        deals, _ = get_deals()
        deal = next((d for d in deals if d["id"] == deal_id), None)
        if not deal or not deal.get("agreementRef"):
            self.send_response(404)
            self.end_headers()
            return

        from integrations.hubspot import HubSpotClient

        settings = load_settings()
        client = HubSpotClient(settings.hubspot_token)
        try:
            agreement = client.get_agreement(deal["agreementRef"])
        except Exception as exc:
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            body = str(exc).encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition", f'inline; filename="{agreement.filename}"')
        self.send_header("Content-Length", str(len(agreement.content)))
        self.end_headers()
        try:
            self.wfile.write(agreement.content)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            # Harmless: the browser cancelled the request mid-stream (modal
            # closed, a different deal's PDF opened, tab navigated away).
            # Nothing to clean up -- just don't let it print a scary
            # traceback for what is normal client behavior.
            pass

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/deals":
            force = parse_qs(parsed.query).get("refresh", ["0"])[0] == "1"
            deals, error = get_deals(force_refresh=force)
            self._send_json({"deals": deals, "error": error, "computed_at": _cache["computed_at"]})
            return

        if path == "/api/cash":
            force = parse_qs(parsed.query).get("refresh", ["0"])[0] == "1"
            cash, error = get_cash(force_refresh=force)
            self._send_json({**cash, "error": error, "computed_at": _cash_cache["computed_at"]})
            return

        if path.startswith("/api/deals/") and path.endswith("/pdf"):
            deal_id = path[len("/api/deals/"):-len("/pdf")]
            self._serve_deal_pdf(deal_id)
            return

        if path == "/":
            path = "/emma_workspace.html"

        target = (UI_DIR / path.lstrip("/")).resolve()
        try:
            target.relative_to(UI_DIR.resolve())
        except ValueError:
            self.send_response(404)
            self.end_headers()
            return
        if not target.is_file():
            self.send_response(404)
            self.end_headers()
            return

        data = target.read_bytes()
        content_type = "text/html; charset=utf-8" if target.suffix == ".html" else "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8600
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Emma UI (live HubSpot deals) — http://127.0.0.1:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
