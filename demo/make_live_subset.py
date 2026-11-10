"""Write the small batch for the live PayPal sandbox run.

Reads   demo/live_seeded_batch.csv, demo/live_seeded_history.csv, demo/sandbox_accounts.json  (gitignored)
Writes  demo/live_subset_batch.csv    the rows paid to the sandbox accounts (clean, clean2, clean3: unflagged,
                                      they start approved) plus the six seeded incident rows, which the model flags
                                      and the operator must decide; at most 10 rows in all
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
INCIDENT_ROWS = {"r070", "r073", "r116", "r122", "r129", "r144"}   # the seeded incidents and their partners
MAX_ROWS = 10


def main():
    accounts = {e.lower() for e in json.loads((DEMO / "sandbox_accounts.json").read_text(encoding="utf-8")).values()}
    src = DEMO / "live_seeded_batch.csv"
    with src.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields, rows = reader.fieldnames, list(reader)
    keep = [r for r in rows if r["receiver_email"].lower() in accounts or r["row_id"] in INCIDENT_ROWS]
    paid = [r["row_id"] for r in keep if r["receiver_email"].lower() in accounts]
    if len(paid) != len(accounts):
        raise SystemExit(f"expected one row per sandbox account ({len(accounts)}), found {len(paid)}: {paid}. "
                         "Run demo/make_live_from_accounts.py after editing sandbox_accounts.json")
    missing = sorted(INCIDENT_ROWS - {r["row_id"] for r in keep})
    if missing:
        raise SystemExit(f"incident rows missing from {src.name}: {missing}")
    if len(keep) > MAX_ROWS:
        raise SystemExit(f"{len(keep)} rows; the live subset is limited to {MAX_ROWS}")
    if {r["currency"] for r in keep} != {"USD"}:
        raise SystemExit("live subset must be USD only")

    with (DEMO / "live_subset_batch.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(keep)
    (DEMO / "live_subset_history.csv").write_bytes((DEMO / "live_seeded_history.csv").read_bytes())

    total = sum(Decimal(r["amount"]) for r in keep)
    print(f"wrote demo/live_subset_batch.csv: {len(keep)} rows, total {total} USD")
    print(f"  rows paid to sandbox accounts (start approved if unflagged): {paid}")
    print(f"  seeded incident rows (flagged, need a decision): {sorted(INCIDENT_ROWS)}")
    print("wrote demo/live_subset_history.csv (unchanged copy)")


if __name__ == "__main__":
    main()
