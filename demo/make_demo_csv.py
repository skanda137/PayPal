"""Write the review-demo CSVs in the 8 contract columns from the ML's synthetic demo set.

    python demo/make_demo_csv.py

demo_batch.csv   = USD rows of ml/artifacts/demo/data/current_batch.csv (USD only, so approved rows are sendable)
demo_history.csv = all of ml/artifacts/demo/data/history.csv (every currency; it is only scoring context)

All names and emails are synthetic. Do NOT send this batch: ~110 rows default to approved and go to non-existent
addresses. Use it to demo the review screen; use make_live_csv.py for the live sandbox send.
"""
import csv
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "ml" / "artifacts" / "demo" / "data"
COLUMNS = ["row_id", "sender_item_id", "receiver_email", "recipient_name", "amount", "currency", "note", "date"]


def convert(src: Path, dst: Path, usd_only: bool) -> int:
    with open(src, encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f) if not usd_only or r["currency"] == "USD"]
    with open(dst, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(dict(row_id=r["row_id"], sender_item_id=r["row_id"], receiver_email=r["payee_email"],
                            recipient_name=r["payee_name"], amount=r["amount"], currency=r["currency"],
                            note=r["note"], date=r["batch_date"]))
    return len(rows)


if __name__ == "__main__":
    print("demo_batch.csv rows:", convert(SRC / "current_batch.csv", HERE / "demo_batch.csv", usd_only=True))
    print("demo_history.csv rows:", convert(SRC / "history.csv", HERE / "demo_history.csv", usd_only=False))
