"""Make the committable seeded demo files from the local live ones.

Reads   demo/live_seeded_batch.csv, demo/live_seeded_history.csv, demo/sandbox_accounts.json  (gitignored)
Writes  demo/seeded_batch.csv, demo/seeded_history.csv                                      (committed)

Each real sandbox email is replaced by a placeholder for its role, and the typo row r116 gets the
typo_base placeholder with one inserted character, as in the live file. sandbox_accounts.json may hold any
subset of the roles below; roles it does not list are already placeholders in the live files. Both versions are then scored
through the real API. The files are written only if every row gets the same action and flags.

Placeholders were chosen by trial: name-shaped emails (e.g. anil_anderson@...) made the model link the
typo row to an unrelated "Anil Anderson" (r127) and pushed r073 from review to hold, because the real
sandbox addresses (sb-...@...example.com) look nothing like a person's name.

Prints no real email addresses. Run from the repo root:  python demo/make_seeded_placeholders.py
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

PLACEHOLDER = {
    "clean": "william.siddiqui@gmail.com",
    "repeat": "andrewphillips@phillipsco.com",
    "shift": "j.stewart@yahoo.com",
    "typo_base": "billing-0457219@kestrelworks.io",
    "alias_canonical": "andre.martin@martinmail.com",
    "alias_dup": "ateliersol@proton.me",
    "clean2": "oliviajones2@outlook.com",   # r016, an unflagged payee: the 2nd registered account in the live run
    "clean3": "tevans@yahoo.com",           # r028, an unflagged payee: the 3rd registered account in the live run
}
TYPO_ROW, TYPO_EMAIL = "r116", "billing-04577219@kestrelworks.io"


def convert(path: Path, real_to_ph: dict) -> tuple[bytes, list[dict]]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    fields = list(rows[0].keys())
    for r in rows:
        email = r["receiver_email"].lower()
        if email in real_to_ph:
            r["receiver_email"] = real_to_ph[email]
        elif path.name.endswith("batch.csv") and r["row_id"] == TYPO_ROW:
            r["receiver_email"] = TYPO_EMAIL
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode(), rows


def score(client, batch: bytes, history: bytes) -> dict:
    r = client.post("/api/batches", files={"file": ("b.csv", batch, "text/csv"), "history": ("h.csv", history, "text/csv")})
    if r.status_code != 200:
        raise SystemExit(f"upload failed: {r.status_code} {r.text[:500]}")
    return {x["row_id"]: (x["action"], tuple(sorted(f["type"] for f in x["flags"]))) for x in r.json()["rows"]}


def main():
    accounts = json.loads((DEMO / "sandbox_accounts.json").read_text(encoding="utf-8"))
    unknown = sorted(set(accounts) - set(PLACEHOLDER))
    if not accounts or unknown:
        raise SystemExit(f"sandbox_accounts.json needs at least one of the roles {sorted(PLACEHOLDER)}; unknown roles: {unknown}")
    real_to_ph = {accounts[k].lower(): PLACEHOLDER[k] for k in accounts}

    live_b = (DEMO / "live_seeded_batch.csv").read_bytes()
    live_h = (DEMO / "live_seeded_history.csv").read_bytes()
    ph_b, rows_b = convert(DEMO / "live_seeded_batch.csv", real_to_ph)
    ph_h, rows_h = convert(DEMO / "live_seeded_history.csv", real_to_ph)

    text = (ph_b + ph_h).decode().lower()
    domains = {e.split("@")[1] for e in real_to_ph}
    if any(e in text for e in real_to_ph) or any("@" + d in text for d in domains):
        raise SystemExit("a real sandbox address or domain is still in the placeholder output")
    ours = set(PLACEHOLDER.values()) | {TYPO_EMAIL}
    taken = {r["receiver_email"].lower() for r in rows_b + rows_h} - ours
    if ours & taken:
        raise SystemExit(f"placeholder already used by another payee: {sorted(ours & taken)}")

    from fastapi.testclient import TestClient
    from backend.app import create_app
    with tempfile.TemporaryDirectory() as tmp:
        live = score(TestClient(create_app(db_path=str(Path(tmp) / "live.db"))), live_b, live_h)
        ph = score(TestClient(create_app(db_path=str(Path(tmp) / "ph.db"))), ph_b, ph_h)

    diff = {k: (live[k], ph[k]) for k in live if live[k] != ph[k]}
    flagged = sorted(k for k, v in ph.items() if v[0] != "proceed" or v[1])
    print(f"rows: {len(ph)}  flagged: {flagged}")
    if diff:
        raise SystemExit(f"placeholders change scoring, not written: {diff}")
    (DEMO / "seeded_batch.csv").write_bytes(ph_b)
    (DEMO / "seeded_history.csv").write_bytes(ph_h)
    print("same actions and flags as the live files; wrote demo/seeded_batch.csv and demo/seeded_history.csv")


if __name__ == "__main__":
    main()
