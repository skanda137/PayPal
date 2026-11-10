import json
import os
import sys
import time
import uuid
from pathlib import Path

import requests

BASE = "https://api-m.sandbox.paypal.com"
PAYOUTS_SCOPE = "https://uri.paypal.com/payments/payouts"
TERMINAL_BATCH = {"SUCCESS", "DENIED", "CANCELED"}
POLL_SECONDS, POLL_LIMIT_S = 5, 180

log = []


class TokenError(Exception):
    """The token request failed. Carries only PayPal's error code and description."""


def load_dotenv(override=False):
    """Read KEY=value lines from a .env beside this script. With override=True the file wins over the shell."""
    path = Path(__file__).resolve().parent / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip("\"'")
            if override or key not in os.environ:
                os.environ[key] = value


def env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing env var {name}")
    return value


def call(step, method, path, token, body=None):
    started = time.time()
    r = requests.request(
        method, BASE + path, json=body, timeout=30,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        payload = r.json()
    except ValueError:
        payload = {"raw": r.text[:500]}
    log.append({
        "step": step, "status": r.status_code, "seconds": round(time.time() - started, 2),
        "paypal_debug_id": r.headers.get("Paypal-Debug-Id"), "request": body, "response": payload,
    })
    return r.status_code, payload


def item(receiver, sender_item_id, value="1.00"):
    return {
        "recipient_type": "EMAIL", "receiver": receiver, "sender_item_id": sender_item_id,
        "amount": {"currency": "USD", "value": value}, "note": "PayoutGuard smoke test",
    }


def batch(items, batch_id=None):
    return {
        "sender_batch_header": {
            "sender_batch_id": batch_id or f"smoke-{uuid.uuid4().hex[:12]}",
            "email_subject": "PayoutGuard smoke test",
        },
        "items": items,
    }


def main():
    client_id, secret = env("PAYPAL_CLIENT_ID"), env("PAYPAL_CLIENT_SECRET")
    receivers = [r.strip() for r in env("RECEIVERS").split(",") if r.strip()]
    typo = env("TYPO_RECEIVER")

    # 1. Token and scope: is Payouts enabled for this app?
    r = requests.post(f"{BASE}/v1/oauth2/token", auth=(client_id, secret),
                      data={"grant_type": "client_credentials"}, timeout=30)
    if r.status_code != 200:
        try:
            body = r.json()
        except ValueError:
            body = {}
        log.append({"step": "oauth", "status": r.status_code, "error": body.get("error"),
                    "error_description": body.get("error_description"),
                    "paypal_debug_id": r.headers.get("Paypal-Debug-Id")})
        raise TokenError(f"token request failed: HTTP {r.status_code}, error={body.get('error')!r}, "
                         f"error_description={body.get('error_description')!r}")
    auth = r.json()
    token = auth["access_token"]
    has_scope = PAYOUTS_SCOPE in auth.get("scope", "").split()
    log.append({"step": "oauth", "status": r.status_code, "payouts_scope_present": has_scope})

    # 2. Main batch: valid receivers plus one mistyped email.
    main_items = [item(rcv, f"main-{i}") for i, rcv in enumerate(receivers)] + [item(typo, "main-typo")]
    main_body = batch(main_items)
    status, created = call("create_main_batch", "POST", "/v1/payments/payouts", token, main_body)
    if status != 201:
        raise RuntimeError(f"main batch not created: {status} {json.dumps(created)[:400]}")
    batch_id = created["batch_header"]["payout_batch_id"]

    # 3. Replay with the same sender_batch_id: docs say it is rejected and links to the original.
    call("replay_same_sender_batch_id", "POST", "/v1/payments/payouts", token, main_body)

    # 4. What PayPal catches natively: same sender_item_id twice in one batch.
    call("dup_sender_item_id_in_batch", "POST", "/v1/payments/payouts", token,
         batch([item(receivers[0], "dup-1"), item(receivers[0], "dup-1")]))

    # 5. What PayPal may miss: same person, different sender_item_ids (semantic duplicate).
    call("same_receiver_different_item_ids", "POST", "/v1/payments/payouts", token,
         batch([item(receivers[0], "semdup-1"), item(receivers[0], "semdup-2")]))

    # 6. Poll the main batch until terminal, recording how long sandbox takes.
    waited, final = 0, None
    while waited <= POLL_LIMIT_S:
        _, final = call("poll_main_batch", "GET", f"/v1/payments/payouts/{batch_id}?total_required=true", token)
        if final.get("batch_header", {}).get("batch_status") in TERMINAL_BATCH:
            break
        time.sleep(POLL_SECONDS)
        waited += POLL_SECONDS

    by_step = {e["step"]: e for e in log}
    replay, dup, semdup = (by_step[s] for s in
                           ("replay_same_sender_batch_id", "dup_sender_item_id_in_batch", "same_receiver_different_item_ids"))
    statuses = {i["payout_item"]["sender_item_id"]: i.get("transaction_status") for i in final.get("items", [])}
    typo_item = next((i for i in final.get("items", []) if i["payout_item"]["sender_item_id"] == "main-typo"), {})
    return {
        "mistyped_email_item": {"transaction_status": typo_item.get("transaction_status"),
                                "error_name": (typo_item.get("errors") or {}).get("name")},
        "replay_same_sender_batch_id": {
            "http_status": replay["status"], "error_name": replay["response"].get("name"),
            "links_to_original_batch": any(batch_id in (l.get("href") or "") for l in replay["response"].get("links", [])),
        },
        "dup_sender_item_id_in_batch": {"http_status": dup["status"], "error_name": dup["response"].get("name")},
        "same_receiver_different_item_ids": {
            "http_status": semdup["status"], "accepted_at_create": semdup["status"] == 201,
            "error_name": semdup["response"].get("name"),
        },
        "payouts_scope_present": has_scope,
        "main_batch_id": batch_id,
        "main_batch_final_status": final.get("batch_header", {}).get("batch_status"),
        "seconds_to_terminal_or_timeout": waited,
        "main_item_statuses": statuses,
        "http_status_by_step": {e["step"]: e["status"] for e in log if e["step"] != "poll_main_batch"},
    }


def print_summary(s):
    """Plain summary. Sender item ids only: no emails, no secrets."""
    r, d, m, t = (s["replay_same_sender_batch_id"], s["dup_sender_item_id_in_batch"],
                  s["same_receiver_different_item_ids"], s["mistyped_email_item"])
    done = s["main_batch_final_status"] in TERMINAL_BATCH
    print("SMOKE TEST SUMMARY (PayPal sandbox)")
    print(f"  payouts scope present:              {s['payouts_scope_present']}")
    print(f"  main batch:                         {s['main_batch_final_status']} after {s['seconds_to_terminal_or_timeout']} s"
          + ("" if done else f" (not final within {POLL_LIMIT_S} s)"))
    print(f"  item statuses:                      {s['main_item_statuses']}")
    print(f"  mistyped-email item (main-typo):    {t['transaction_status']}, error name {t['error_name']}")
    print(f"  replay, same sender_batch_id:       HTTP {r['http_status']}, error name {r['error_name']}, "
          f"links to original batch: {r['links_to_original_batch']}")
    print(f"  same sender_item_id twice:          HTTP {d['http_status']}, error name {d['error_name']}")
    print(f"  same receiver, different item ids:  {'accepted' if m['accepted_at_create'] else 'rejected'} at create "
          f"(HTTP {m['http_status']}, error name {m['error_name']})")
    print("  full requests and responses: smoke_report.json (gitignored)")


if __name__ == "__main__":
    load_dotenv(override=True)
    summary, error = None, None
    try:
        summary = main()
        print_summary(summary)
    except TokenError as e:  # print PayPal's error code and description only, no traceback
        error = str(e)
        print(error)
        sys.exit(1)
    except Exception as e:
        error = str(e)
        raise
    finally:  # a failed run still leaves the log of what PayPal answered
        with open("smoke_report.json", "w") as f:
            json.dump({"summary": summary, "error": error, "log": log}, f, indent=2)
