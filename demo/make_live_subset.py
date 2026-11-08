"""Write the small batch for the live PayPal sandbox run.

Reads   demo/live_seeded_batch.csv, demo/live_seeded_history.csv, demo/sandbox_accounts.json  (gitignored)
Writes  demo/live_subset_batch.csv    only the rows paid to the sandbox accounts, plus the typo row r116
        demo/live_subset_history.csv  byte-for-byte copy of live_seeded_history.csv
Both outputs match the demo/live_*.csv ignore rule.

Never send the full 212-row seeded batch: 206 rows start approved and most go to made-up addresses.

Prints no email addresses. Run from the repo root:  python demo/make_live_subset.py
"""
import csv
import json
from decimal import Decimal
from pathlib import Path

DEMO = Path(__file__).resolve().parent
TYPO_ROW = "r116"


def main():
    accounts = {e.lower() for e in json.loads((DEMO / "sandbox_accounts.json").read_text(encoding="utf-8")).values()}
    src = DEMO / "live_seeded_batch.csv"
    with src.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields, rows = reader.fieldnames, list(reader)
    keep = [r for r in rows if r["receiver_email"].lower() in accounts or r["row_id"] == TYPO_ROW]
    if not any(r["row_id"] == TYPO_ROW for r in keep):
        raise SystemExit(f"{TYPO_ROW} not found in {src.name}")
    if {r["currency"] for r in keep} != {"USD"}:
        raise SystemExit("live subset must be USD only")

    with (DEMO / "live_subset_batch.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(keep)
    (DEMO / "live_subset_history.csv").write_bytes((DEMO / "live_seeded_history.csv").read_bytes())

    total = sum(Decimal(r["amount"]) for r in keep)
    print(f"wrote demo/live_subset_batch.csv: {len(keep)} rows {[r['row_id'] for r in keep]}, total {total} USD")
    print("wrote demo/live_subset_history.csv (unchanged copy)")


if __name__ == "__main__":
    main()
