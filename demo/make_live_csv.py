"""Write the 3-row live sandbox CSVs from .env (RECEIVERS, TYPO_RECEIVER). Output files are gitignored.

    python demo/make_live_csv.py

live_batch.csv   = two real sandbox Personal receivers + the mistyped email, USD 1.00 each.
live_history.csv = two earlier payouts to each real receiver, so the mistyped row is scored against a known
                   payee (expected flag: typo_receiver, action hold; sending it needs the operator override).
"""
import csv
import os
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
COLUMNS = ["row_id", "sender_item_id", "receiver_email", "recipient_name", "amount", "currency", "note", "date"]
NAMES = ["Ava Thompson", "Rahul Mehta"]


def one_edit(a: str, b: str) -> bool:
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    s, t = (a, b) if len(a) < len(b) else (b, a)
    return any(t[:i] + t[i + 1:] == s for i in range(len(t)))


def main():
    load_dotenv(HERE.parent / ".env", override=False)
    receivers = [r.strip() for r in os.environ.get("RECEIVERS", "").split(",") if r.strip()]
    typo = os.environ.get("TYPO_RECEIVER", "").strip()
    if len(receivers) < 2 or not typo:
        raise SystemExit("set RECEIVERS (at least 2, comma-separated) and TYPO_RECEIVER in .env")
    source = [i for i, r in enumerate(receivers) if one_edit(r, typo)]
    if not source:
        raise SystemExit("TYPO_RECEIVER must be exactly one character edit away from one of RECEIVERS")
    receivers = receivers[:2] if source[0] < 2 else [receivers[0], receivers[source[0]]]
    src_name = NAMES[next(j for j, r in enumerate(receivers) if one_edit(r, typo))]  # typo row keeps the real name

    batch = [dict(row_id=f"L{i + 1}", sender_item_id=f"live-{i + 1}", receiver_email=e, recipient_name=NAMES[i],
                  amount="1.00", currency="USD", note="PayoutGuard live sandbox run", date="2026-11-10")
             for i, e in enumerate(receivers)]
    batch.append(dict(row_id="L3", sender_item_id="live-3", receiver_email=typo, recipient_name=src_name,
                      amount="1.00", currency="USD", note="PayoutGuard live sandbox run", date="2026-11-10"))
    history = [dict(row_id=f"H{i + 1}{m}", sender_item_id=f"hist-{i + 1}-{m}", receiver_email=e, recipient_name=NAMES[i],
                    amount="1.00", currency="USD", note="Earlier payout", date=f"2026-{m:02d}-28")
               for i, e in enumerate(receivers) for m in (9, 10)]
    for name, rows in (("live_batch.csv", batch), ("live_history.csv", history)):
        with open(HERE / name, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
        print(f"wrote demo/{name} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
