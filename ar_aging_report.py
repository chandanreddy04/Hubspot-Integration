#!/usr/bin/env python3
"""Standalone AR aging report -- run on demand, no server, no scheduler.

Per the manager's guidance: full automation (something that runs this
itself on a schedule and sends reminders) is deferred until the app is
actually deployed online. For now, this is something a person runs
directly -- `python ar_aging_report.py` -- and every run fetches the
real current date and recomputes aging from scratch against real
QuickBooks data; nothing here is cached or assumed from a prior run.

Reuses ui/server.py's real HubSpot + QuickBooks plumbing (same real
deal-customer scoping as Cash Application/Collections) rather than
querying QuickBooks a third, separate way.

Usage:
    python ar_aging_report.py            # human-readable report
    python ar_aging_report.py --json     # machine-readable, for piping elsewhere
    python ar_aging_report.py --send     # also runs the reminder cycle (needs GMAIL_MODE=live)

--send reuses run_reminder_cycle() -- the same -7/0/+14-day cadence the
Collections page's "Run reminder cycle" button calls, with the same
per-invoice-per-stage dedup (rally_state.db), so running this repeatedly
never double-emails a stage that's already gone out. This used to call a
separate, simpler ">N days overdue, no dedup" sender; merged into one
system on request. This report's own unbounded aging buckets are
unrelated to and unaffected by that cadence -- the report always shows
every outstanding invoice regardless of what --send would act on.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "ui"))

from server import _real_outstanding_invoices, run_reminder_cycle  # noqa: E402  (path setup must run first)
from config import load_settings  # noqa: E402

# Standard AR aging buckets. "Current" covers anything not yet past due
# (dpd <= 0), including invoices due days or months from now -- an aging
# report has to account for the full outstanding balance, not just what's
# near-due the way the reminder cadence (-7/0/+14) does.
_AGING_BUCKET_ORDER = ["Current", "1-30 days", "31-60 days", "61-90 days", "90+ days"]


def _bucket_for(dpd: int) -> str:
    if dpd <= 0:
        return "Current"
    if dpd <= 30:
        return "1-30 days"
    if dpd <= 60:
        return "31-60 days"
    if dpd <= 90:
        return "61-90 days"
    return "90+ days"


def compute_aging_report() -> dict:
    today = date.today()
    rows = _real_outstanding_invoices()

    buckets: dict[str, list[dict]] = {label: [] for label in _AGING_BUCKET_ORDER}
    for r in rows:
        buckets[_bucket_for(r["dpd"])].append({
            "customer": r["customer"],
            "invoice": r["docnum"],
            "amount": round(r["balance"], 2),
            "dueDate": r["dueDate"].isoformat(),
            "daysOverdue": r["dpd"],
        })

    for items in buckets.values():
        items.sort(key=lambda x: -x["daysOverdue"])

    totals = {label: round(sum(item["amount"] for item in items), 2) for label, items in buckets.items()}
    grand_total = round(sum(totals.values()), 2)

    return {"asOf": today.isoformat(), "buckets": buckets, "totals": totals, "grandTotal": grand_total}


def _print_report(report: dict) -> None:
    print(f"AR Aging Report — as of {report['asOf']}")
    print("=" * 72)
    for label in _AGING_BUCKET_ORDER:
        items = report["buckets"][label]
        print(f"\n{label}  (total: ${report['totals'][label]:,.2f})")
        if not items:
            print("  (none)")
            continue
        for r in items:
            age = f"{r['daysOverdue']}d overdue" if r["daysOverdue"] > 0 else f"due in {-r['daysOverdue']}d" if r["daysOverdue"] < 0 else "due today"
            print(f"  {r['customer']:<32} inv #{r['invoice']:<10} ${r['amount']:>12,.2f}   due {r['dueDate']}  ({age})")
    print("\n" + "=" * 72)
    print(f"Grand total outstanding: ${report['grandTotal']:,.2f}")


def _send_reminders() -> None:
    settings = load_settings()
    if settings.gmail_mode != "live":
        print("\nGMAIL_MODE is not 'live' in .env -- can't send email. Skipping --send.", file=sys.stderr)
        sys.exit(1)

    override = settings.outstanding_reminder_override_email
    print("\nRunning the -7/0/+14-day reminder cycle...")
    print(f"Recipient override: {override or '(none -- sending to real QuickBooks billing emails)'}")

    try:
        result = run_reminder_cycle()
    except Exception as exc:
        print(f"Could not send reminders: {exc}", file=sys.stderr)
        sys.exit(1)

    if result["collectionsError"] and not result["checked"]:
        print(f"Warning -- underlying QuickBooks pull failed: {result['collectionsError']}", file=sys.stderr)

    for row in result["sent"]:
        print(f"  sent    -> {row['c']}  (stage {row['stage']}, to {row['to']}, message {row['messageId']})")
    for row in result["failed"]:
        print(f"  FAILED  -> {row['c']}: {row['reason']}")
    for row in result["skipped"]:
        print(f"  skipped -> {row['c']}: {row['reason']}")
    print(f"\n{len(result['sent'])} sent, {len(result['failed'])} failed, {len(result['skipped'])} skipped, "
          f"{result['due']} due out of {result['checked']} checked.")


def main() -> None:
    try:
        report = compute_aging_report()
    except Exception as exc:
        print(f"Could not compute AR aging: {exc}", file=sys.stderr)
        sys.exit(1)

    if "--json" in sys.argv:
        print(json.dumps(report, indent=2))
    else:
        _print_report(report)

    if "--send" in sys.argv:
        _send_reminders()


if __name__ == "__main__":
    main()
