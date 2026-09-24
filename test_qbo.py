#!/usr/bin/env python3
"""First real test of the QuickBooks sandbox connection.

Doesn't assume what data is in there — just connects and shows what's
actually present, since the sandbox comes pre-loaded with sample invoices
and payments rather than starting empty like the HubSpot test account did.

Usage:
    python test_qbo.py
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from config import load_settings
from integrations.qbo import QboClient


def main() -> None:
    settings = load_settings()
    print("QuickBooks sandbox connection test")
    print(f"  QBO_MODE = {settings.qbo_mode}")

    if settings.qbo_mode != "live":
        print("\n  Set QBO_MODE=live in .env (plus the QBO_* credentials) to actually test this.")
        return

    client = QboClient(settings)

    print("\n[1] List invoices already in the sandbox")
    print("-" * 40)
    invoices = client.list_invoices(limit=20)
    print(f"  found {len(invoices)} invoice(s)")
    for inv in invoices:
        customer = inv.get("CustomerRef", {}).get("name", "?")
        print(
            f"  #{inv.get('DocNumber', '?'):<8} {customer:<28} "
            f"total={inv.get('TotalAmt')}  balance={inv.get('Balance')}"
        )

    print("\n[2] List payments (all history — cursor from the beginning)")
    print("-" * 40)
    payments, cursor = client.list_payments_since(None)
    print(f"  found {len(payments)} payment(s), new cursor = {cursor}")
    for p in payments:
        linked = ", ".join(p.linked_invoice_numbers) or "(no linked invoice)"
        print(f"  {p.payment_id:<8} {p.customer_name:<28} ${p.total_amount:<10} -> {linked}")


if __name__ == "__main__":
    main()
