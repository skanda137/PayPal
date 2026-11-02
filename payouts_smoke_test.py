import json
import os
import time
import uuid

import requests

BASE = "https://api-m.sandbox.paypal.com"
PAYOUTS_SCOPE = "https://uri.paypal.com/payments/payouts"
TERMINAL_BATCH = {"SUCCESS", "DENIED", "CANCELED"}
POLL_SECONDS, POLL_LIMIT_S = 5, 180

log = []


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
    r.raise_for_status()
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

    summary = {
        "payouts_scope_present": has_scope,
        "main_batch_id": batch_id,
        "main_batch_final_status": final.get("batch_header", {}).get("batch_status"),
        "seconds_to_terminal_or_timeout": waited,
        "main_item_statuses": {
            i["payout_item"]["sender_item_id"]: i.get("transaction_status") for i in final.get("items", [])
        },
        "http_status_by_step": {e["step"]: e["status"] for e in log if e["step"] != "poll_main_batch"},
    }
    with open("smoke_report.json", "w") as f:
        json.dump({"summary": summary, "log": log}, f, indent=2)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
