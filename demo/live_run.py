"""Scripted live sandbox run against the running backend (the backend makes the PayPal calls, sandbox only).

    python demo/make_live_subset.py                         # once: writes demo/live_subset_batch.csv
    uvicorn backend.app:create_app --factory --port 8000    # in another terminal
    python demo/live_run.py                                 # approves only r163, holds the rest, sends, polls
    python demo/live_run.py --override r116                 # also overrides the hold on r116 and sends it

Steps: upload the 7-row subset with the seeded history, print the flag summary, approve r163 and hold every other
row (rows named by --override are approved with override=true), send, poll every 3 s for up to 180 s, print each
item's status and error text, and save the audit events to demo/live_run_report.json (gitignored).

Prints row ids, amounts and statuses. Prints no email addresses and no secrets.
"""
import argparse
import json
import sys
import time
from decimal import Decimal
from pathlib import Path

import requests

DEMO = Path(__file__).resolve().parent
BATCH, HISTORY = DEMO / "live_subset_batch.csv", DEMO / "live_seeded_history.csv"
REPORT = DEMO / "live_run_report.json"
APPROVE = "r163"          # the one row the model lets through
MAX_ROWS = 10             # the full 212-row seeded batch must never be sent
POLL_SECONDS, POLL_LIMIT_S = 3, 180
STILL_MOVING = {None, "PENDING"}


def fail(message):
    print("STOPPED:", message)
    sys.exit(1)


def call(method, url, **kwargs):
    r = requests.request(method, url, timeout=120, **kwargs)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.text[:500]
        raise RuntimeError(f"{method} {url.split('/api/')[-1]} -> HTTP {r.status_code}: {json.dumps(detail)[:800]}")
    return r.json()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="backend URL")
    ap.add_argument("--override", nargs="+", default=[], metavar="ROW_ID",
                    help="held or review rows to approve as an operator override and send")
    args = ap.parse_args()
    api = args.base.rstrip("/") + "/api"

    for p in (BATCH, HISTORY):
        if not p.exists():
            fail(f"{p.relative_to(DEMO.parent)} not found. Run: python demo/make_live_subset.py")
    try:
        requests.get(args.base.rstrip("/") + "/openapi.json", timeout=5).raise_for_status()
    except requests.RequestException as e:
        fail(f"backend not reachable at {args.base} ({type(e).__name__}). "
             "Start it: uvicorn backend.app:create_app --factory --port 8000")

    report = {"started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "override": args.override}
    batch_id = None
    try:
        with BATCH.open("rb") as b, HISTORY.open("rb") as h:
            batch = call("POST", f"{api}/batches", files={"file": (BATCH.name, b, "text/csv"),
                                                          "history": (HISTORY.name, h, "text/csv")})
        batch_id = batch["batch_id"]
        rows = {r["row_id"]: r for r in batch["rows"]}
        if len(rows) > MAX_ROWS:
            fail(f"{len(rows)} rows uploaded; this script only sends the small subset (max {MAX_ROWS})")
        unknown = [r for r in [APPROVE, *args.override] if r not in rows]
        if unknown:
            fail(f"rows not in the batch: {unknown}")

        print(f"1. UPLOAD  batch {batch_id}: {batch['summary']['n_rows']} rows, {batch['summary']['n_history']} history rows, "
              f"model actions {batch['summary']['actions']}")
        for r in batch["rows"]:
            print(f"     {r['row_id']:<5} {r['amount']:>8} {r['currency']}  {r['action']:<8} "
                  f"{', '.join(f['type'] for f in r['flags']) or '-'}")

        hold = {rid: "hold" for rid in rows if rid != APPROVE and rid not in args.override}
        batch = call("PUT", f"{api}/batches/{batch_id}/decisions", json={APPROVE: "approve", **hold})
        if args.override:
            batch = call("PUT", f"{api}/batches/{batch_id}/decisions", params={"override": "true"},
                         json={rid: "approve" for rid in args.override})
        approved = [r for r in batch["rows"] if r["decision"] == "approve"]
        total = sum(Decimal(r["amount"]) for r in approved)
        print(f"2. DECIDE  approve {[r['row_id'] for r in approved]} (override: {args.override or 'none'}), "
              f"hold {sorted(r['row_id'] for r in batch['rows'] if r['decision'] == 'hold')}")

        print(f"3. SEND    {len(approved)} item(s), {total} USD, to the PayPal sandbox")
        view = call("POST", f"{api}/batches/{batch_id}/send")
        print(f"     payout_batch_id {view['payout_batch_id']}, sender_batch_id {view['sender_batch_id']}, "
              f"batch status {view['batch_status']}")

        waited = 0
        while waited <= POLL_LIMIT_S:
            view = call("GET", f"{api}/batches/{batch_id}/payout")
            if view["terminal"] and not any(i["transaction_status"] in STILL_MOVING for i in view["items"]):
                break
            time.sleep(POLL_SECONDS)
            waited += POLL_SECONDS
        settled = waited <= POLL_LIMIT_S
        print(f"4. STATUS  batch {view['batch_status']} after {waited} s" + ("" if settled else f" (not final within {POLL_LIMIT_S} s)"))
        for i in view["items"]:
            err = i["errors"] or {}
            text = f"  {err.get('name')}: {err.get('message')}" if err else ""
            print(f"     {i['row_id']:<5} {i['transaction_status']}{text}")
        report.update(payout=view, seconds_waited=waited, settled=settled)
    except RuntimeError as e:
        report["error"] = str(e)
        print("STOPPED:", e)
    finally:
        if batch_id:
            report["batch_id"] = batch_id
            report["audit"] = call("GET", f"{api}/batches/{batch_id}/audit")
            REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
            kinds = [e["kind"] for e in report["audit"]]
            print(f"5. AUDIT   {len(kinds)} events: {', '.join(kinds)}")
            print(f"     saved to demo/{REPORT.name} (gitignored; it contains receiver emails)")
    sys.exit(1 if "error" in report else 0)


if __name__ == "__main__":
    main()
