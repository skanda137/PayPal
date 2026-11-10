"""Rebuild the local live files from the committed placeholder files and demo/sandbox_accounts.json.

Reads   demo/seeded_batch.csv, demo/seeded_history.csv   (committed, placeholder emails)
        demo/sandbox_accounts.json                       (gitignored: role -> sandbox Personal account email)
Writes  demo/live_seeded_batch.csv, demo/live_seeded_history.csv   (gitignored)

This is make_seeded_placeholders.py in reverse: each role's placeholder email is replaced by that role's
sandbox account. sandbox_accounts.json may list any of the roles in PLACEHOLDER (make_seeded_placeholders.py):
clean, clean2 and clean3 are the three registered accounts used for the live send. Roles it does not list keep
their placeholder, which is fine: those rows are held and never sent. If typo_base is listed, the typo row r116
gets that account with one character doubled (one edit from a known payee, not a real account); otherwise r116
keeps its placeholder typo address. Both versions are scored through the real API and
the live files are written only if every row gets the same action and flags as the committed version.

Run from the repo root after editing sandbox_accounts.json, then run make_live_subset.py:
    python demo/make_live_from_accounts.py            # add --dry-run to check without writing
Prints row ids and True/False only, never an email address.
"""
import csv
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(DEMO))
from make_seeded_placeholders import PLACEHOLDER, TYPO_EMAIL, TYPO_ROW, score  # noqa: E402


def typo_of(email: str) -> str:
    local, domain = email.split("@")
    i = len(local) // 2
    return f"{local[:i]}{local[i]}{local[i:]}@{domain}"


def convert(path: Path, mapping: dict) -> bytes:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields, rows = reader.fieldnames, list(reader)
    for r in rows:
        r["receiver_email"] = mapping.get(r["receiver_email"].lower(), r["receiver_email"])
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode()


def main():
    dry = "--dry-run" in sys.argv[1:]
    accounts = json.loads((DEMO / "sandbox_accounts.json").read_text(encoding="utf-8"))
    unknown = sorted(set(accounts) - set(PLACEHOLDER))
    if not accounts or unknown:
        raise SystemExit(f"sandbox_accounts.json needs at least one of the roles {sorted(PLACEHOLDER)}; unknown roles: {unknown}")
    emails = [accounts[r].strip().lower() for r in accounts]
    if len(set(emails)) != len(emails):
        raise SystemExit("two roles share an account; each role needs its own address")
    mapping = {PLACEHOLDER[r]: accounts[r].strip() for r in accounts}
    if "typo_base" in accounts:
        typo = typo_of(accounts["typo_base"].strip())
        if typo.lower() in emails:
            raise SystemExit("the generated typo address is itself one of the accounts")
        mapping[TYPO_EMAIL] = typo
    print("roles replaced with accounts:", sorted(accounts), "| r116 typo address:",
          "built from typo_base" if "typo_base" in accounts else "kept as its placeholder")

    ph_b, ph_h = (DEMO / "seeded_batch.csv").read_bytes(), (DEMO / "seeded_history.csv").read_bytes()
    live_b, live_h = convert(DEMO / "seeded_batch.csv", mapping), convert(DEMO / "seeded_history.csv", mapping)
    left = [p for p in mapping if p in (live_b + live_h).decode().lower()]
    if left:
        raise SystemExit(f"{len(left)} placeholder address(es) were not replaced")

    from fastapi.testclient import TestClient
    from backend.app import create_app
    with tempfile.TemporaryDirectory() as tmp:
        ph = score(TestClient(create_app(db_path=str(Path(tmp) / "ph.db"))), ph_b, ph_h)
        live = score(TestClient(create_app(db_path=str(Path(tmp) / "live.db"))), live_b, live_h)

    flagged = sorted(k for k, v in ph.items() if v[0] != "proceed" or v[1])
    for k in flagged:
        print(f"  {k}: {ph[k][0]:<7} same action and flags with the real accounts: {live[k] == ph[k]}")
    diff = sorted(k for k in ph if live[k] != ph[k])
    if diff:
        raise SystemExit(f"scoring differs on rows {diff}; nothing written")
    print(f"all {len(ph)} rows score the same as the committed files")
    if dry:
        print("dry run: nothing written")
        return
    (DEMO / "live_seeded_batch.csv").write_bytes(live_b)
    (DEMO / "live_seeded_history.csv").write_bytes(live_h)
    print("wrote demo/live_seeded_batch.csv and demo/live_seeded_history.csv. Next: python demo/make_live_subset.py")


if __name__ == "__main__":
    main()
