"""PayoutGuard backend. Run from the repo root:  uvicorn backend.app:create_app --factory --port 8000

ML is advisory. Money movement is deterministic and gated here: /send reads only rows whose stored
decision is 'approve'. proceed rows default to approve; review/hold rows start undecided and are never sent
unless an operator approves them. Decisions lock once a payout exists.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from fastapi import Body, FastAPI, File, HTTPException, UploadFile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ml"))
from payoutguard_ml import score_batch  # noqa: E402
from payoutguard_ml.score import _analyzer  # noqa: E402  (cached Analyzer; source of the thresholds used)

from backend.payouts import (TERMINAL_BATCH_STATUSES, PayPalClient, PayPalError, money,  # noqa: E402
                             sender_batch_id)

COLUMNS = ["row_id", "sender_item_id", "receiver_email", "recipient_name", "amount", "currency", "note", "date"]
SEND_CURRENCIES = {"USD"}  # sandbox conversion of other currencies is unverified; demo sends USD only
REFRESH_ITEM_STATUSES = {None, "PENDING", "ONHOLD"}
_AMOUNT = re.compile(r"\d{1,9}(\.\d{1,2})?")
_EMAIL = re.compile(r"[^@\s,;]+@[^@\s,;]+\.[A-Za-z]{2,}")
_CURRENCY = re.compile(r"[A-Z]{3}")

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (batch_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, model_version TEXT NOT NULL,
  er_threshold REAL NOT NULL, z_threshold REAL NOT NULL, n_history INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS rows (batch_id TEXT NOT NULL, row_id TEXT NOT NULL, idx INTEGER NOT NULL,
  sender_item_id TEXT NOT NULL, receiver_email TEXT NOT NULL, recipient_name TEXT NOT NULL, amount TEXT NOT NULL,
  currency TEXT NOT NULL, note TEXT NOT NULL, date TEXT NOT NULL, action TEXT NOT NULL, flags_json TEXT NOT NULL,
  decision TEXT CHECK (decision IN ('approve','hold')), PRIMARY KEY (batch_id, row_id));
CREATE TABLE IF NOT EXISTS payouts (batch_id TEXT PRIMARY KEY, sender_batch_id TEXT NOT NULL UNIQUE,
  payout_batch_id TEXT, batch_status TEXT, last_error_json TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS payout_items (batch_id TEXT NOT NULL, row_id TEXT NOT NULL, sender_item_id TEXT NOT NULL,
  payout_item_id TEXT, transaction_status TEXT, errors_json TEXT, PRIMARY KEY (batch_id, row_id));
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, batch_id TEXT,
  kind TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _artifacts() -> Path:  # same rule as ml/payoutguard_ml/score.py
    return Path(os.environ.get("PAYOUTGUARD_ARTIFACTS", ROOT / "ml" / "artifacts"))


def model_version() -> str:
    h = hashlib.sha256()
    files = sorted((_artifacts() / "models").glob("*.joblib"))
    if not files:
        raise RuntimeError(f"no model files in {_artifacts() / 'models'}")
    for p in files:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


# ---------------------------------------------------------------- validation

def parse_csv(raw: bytes, label: str, unique_sender_item_id: bool) -> tuple[list[dict], list[dict]]:
    """Returns (rows, errors). Every value is kept as a string; amount is checked as a plain decimal, never a float."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], [dict(file=label, line=None, row_id=None, field=None, message="file is not UTF-8")]
    reader = csv.DictReader(io.StringIO(text, newline=""))
    header = reader.fieldnames or []
    missing = [c for c in COLUMNS if c not in header]
    extra = [c for c in header if c not in COLUMNS]
    if missing or extra:
        msg = "; ".join(filter(None, [missing and f"missing columns {missing}", extra and f"unexpected columns {extra}"]))
        return [], [dict(file=label, line=1, row_id=None, field=None, message=msg)]
    rows, errors, seen_row, seen_item = [], [], set(), set()
    for line, rec in enumerate(reader, start=2):
        if None in rec:
            errors.append(dict(file=label, line=line, row_id=rec.get("row_id"), field=None, message="too many fields"))
            continue
        r = {c: (rec[c] or "").strip() for c in COLUMNS}

        def err(field, message):
            errors.append(dict(file=label, line=line, row_id=r["row_id"] or None, field=field, message=message))

        if not r["row_id"]:
            err("row_id", "required")
        elif r["row_id"] in seen_row:
            err("row_id", "duplicate row_id")
        seen_row.add(r["row_id"])
        if not r["sender_item_id"]:
            err("sender_item_id", "required")
        elif unique_sender_item_id and r["sender_item_id"] in seen_item:
            err("sender_item_id", "duplicate sender_item_id (PayPal rejects repeats within a batch)")
        seen_item.add(r["sender_item_id"])
        if not _EMAIL.fullmatch(r["receiver_email"]):
            err("receiver_email", f"not a valid email: {r['receiver_email']!r}")
        if not r["recipient_name"]:
            err("recipient_name", "required")
        if not _AMOUNT.fullmatch(r["amount"]):
            err("amount", f"must be a positive number with at most 2 decimals, got {r['amount']!r}")
        elif Decimal(r["amount"]) <= 0:
            err("amount", f"must be > 0, got {r['amount']!r}")
        if not _CURRENCY.fullmatch(r["currency"]):
            err("currency", f"must be 3 uppercase letters, got {r['currency']!r}")
        try:
            date.fromisoformat(r["date"])
        except ValueError:
            err("date", f"must be YYYY-MM-DD, got {r['date']!r}")
        rows.append(r)
    if not rows and not errors:
        errors.append(dict(file=label, line=None, row_id=None, field=None, message="no data rows"))
    return rows, errors


def _ml_frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["amount"] = df["amount"].astype(float)  # advisory scoring only; payouts use the stored strings
    return df


# ---------------------------------------------------------------- app

def create_app(db_path: str | None = None, paypal_client: PayPalClient | None = None) -> FastAPI:
    load_dotenv(ROOT / ".env", override=False)
    db_path = db_path or os.environ.get("PAYOUTGUARD_DB")
    if not db_path:
        raise RuntimeError("set PAYOUTGUARD_DB (path to the SQLite file), see .env.example")
    app = FastAPI(title="PayoutGuard")
    state = {"paypal": paypal_client}

    @contextmanager
    def db():
        """One connection per request: commit on success, roll back on any exception, always close."""
        c = sqlite3.connect(db_path, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    with db() as c:
        c.executescript(SCHEMA)

    def paypal() -> PayPalClient:
        if state["paypal"] is None:
            missing = [k for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_BASE_URL") if not os.environ.get(k)]
            if missing:
                raise HTTPException(500, f"missing env vars {missing}, see .env.example")
            state["paypal"] = PayPalClient(os.environ["PAYPAL_CLIENT_ID"], os.environ["PAYPAL_CLIENT_SECRET"],
                                           os.environ["PAYPAL_BASE_URL"])
        return state["paypal"]

    def audit(c, batch_id, kind, payload):
        c.execute("INSERT INTO events (ts, batch_id, kind, payload_json) VALUES (?,?,?,?)",
                  (_now(), batch_id, kind, json.dumps(payload, default=str)))

    def batch_view(c, batch_id) -> dict:
        b = c.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
        if b is None:
            raise HTTPException(404, f"batch {batch_id} not found")
        rows = [dict(row_id=r["row_id"], recipient_name=r["recipient_name"], receiver_email=r["receiver_email"],
                     amount=r["amount"], currency=r["currency"], action=r["action"], flags=json.loads(r["flags_json"]),
                     decision=r["decision"])
                for r in c.execute("SELECT * FROM rows WHERE batch_id=? ORDER BY idx", (batch_id,))]
        actions = {a: sum(r["action"] == a for r in rows) for a in ("proceed", "review", "hold")}
        decisions = {d or "undecided": sum(r["decision"] == d for r in rows) for d in ("approve", "hold", None)}
        p = c.execute("SELECT sender_batch_id, payout_batch_id FROM payouts WHERE batch_id=?", (batch_id,)).fetchone()
        summary = dict(n_rows=len(rows), actions=actions, decisions=decisions, n_history=b["n_history"],
                       model_version=b["model_version"], er_threshold=b["er_threshold"], z_threshold=b["z_threshold"],
                       created_at=b["created_at"], locked=p is not None,
                       payout_batch_id=p["payout_batch_id"] if p else None)
        return dict(batch_id=batch_id, summary=summary, rows=rows)

    @app.post("/api/batches")
    async def upload(file: UploadFile = File(...), history: UploadFile | None = File(None)):
        rows, errors = parse_csv(await file.read(), "batch", unique_sender_item_id=True)
        hist_rows = []
        if history is not None:
            hist_rows, herr = parse_csv(await history.read(), "history", unique_sender_item_id=False)
            errors += herr
        if errors:  # nothing is scored or stored if any row is invalid
            raise HTTPException(422, dict(message=f"{len(errors)} validation error(s); nothing was scored", errors=errors))
        flags = score_batch(_ml_frame(rows), _ml_frame(hist_rows) if hist_rows else None, mode="model")
        if [f["row_id"] for f in flags] != [r["row_id"] for r in rows]:
            raise HTTPException(500, "score_batch returned rows out of order")
        an = _analyzer("model")
        batch_id = "b-" + uuid.uuid4().hex[:12]
        mv = model_version()
        with db() as c:
            c.execute("INSERT INTO batches VALUES (?,?,?,?,?,?)",
                      (batch_id, _now(), mv, float(an.matcher.threshold), float(an.anomaly.threshold), len(hist_rows)))
            for i, (r, f) in enumerate(zip(rows, flags)):
                c.execute("INSERT INTO rows VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (batch_id, r["row_id"], i, r["sender_item_id"], r["receiver_email"], r["recipient_name"],
                           r["amount"], r["currency"], r["note"], r["date"], f["action"], json.dumps(f["flags"]),
                           "approve" if f["action"] == "proceed" else None))
            audit(c, batch_id, "upload", dict(filename=file.filename, n_rows=len(rows), n_history=len(hist_rows),
                                              history_filename=history.filename if history else None))
            audit(c, batch_id, "score", dict(model_version=mv, er_threshold=float(an.matcher.threshold),
                                             z_threshold=float(an.anomaly.threshold),
                                             actions={a: sum(f["action"] == a for f in flags) for a in ("proceed", "review", "hold")}))
            return batch_view(c, batch_id)

    @app.get("/api/batches/{batch_id}")
    def get_batch(batch_id: str):
        with db() as c:
            return batch_view(c, batch_id)

    @app.put("/api/batches/{batch_id}/decisions")
    def put_decisions(batch_id: str, decisions: dict[str, str] = Body(...)):
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            batch_view(c, batch_id)  # 404 if missing
            if c.execute("SELECT 1 FROM payouts WHERE batch_id=?", (batch_id,)).fetchone():
                raise HTTPException(409, "decisions are locked: a payout was already started for this batch")
            bad = {k: v for k, v in decisions.items() if v not in ("approve", "hold")}
            if bad:
                raise HTTPException(422, f"decision must be 'approve' or 'hold': {bad}")
            current = {r["row_id"]: r["decision"] for r in
                       c.execute("SELECT row_id, decision FROM rows WHERE batch_id=?", (batch_id,))}
            unknown = [k for k in decisions if k not in current]
            if unknown:
                raise HTTPException(422, f"unknown row_id(s): {unknown}")
            for rid, d in decisions.items():
                if current[rid] != d:
                    c.execute("UPDATE rows SET decision=? WHERE batch_id=? AND row_id=?", (d, batch_id, rid))
                    audit(c, batch_id, "decision", {"row_id": rid, "from": current[rid], "to": d})
            return batch_view(c, batch_id)

    def payout_view(c, batch_id) -> dict:
        p = c.execute("SELECT * FROM payouts WHERE batch_id=?", (batch_id,)).fetchone()
        if p is None:
            raise HTTPException(404, f"no payout for batch {batch_id}")
        items = [dict(row_id=i["row_id"], sender_item_id=i["sender_item_id"], payout_item_id=i["payout_item_id"],
                      transaction_status=i["transaction_status"],
                      errors=json.loads(i["errors_json"]) if i["errors_json"] else None)
                 for i in c.execute("SELECT * FROM payout_items WHERE batch_id=? ORDER BY row_id", (batch_id,))]
        return dict(batch_id=batch_id, sender_batch_id=p["sender_batch_id"], payout_batch_id=p["payout_batch_id"],
                    batch_status=p["batch_status"], terminal=p["batch_status"] in TERMINAL_BATCH_STATUSES,
                    last_error=json.loads(p["last_error_json"]) if p["last_error_json"] else None, items=items)

    @app.post("/api/batches/{batch_id}/send")
    def send(batch_id: str):
        with db() as c:
            c.execute("BEGIN IMMEDIATE")  # serialises concurrent sends for this database
            batch_view(c, batch_id)
            approved = c.execute("SELECT * FROM rows WHERE batch_id=? AND decision='approve' ORDER BY idx",
                                 (batch_id,)).fetchall()
            if not approved:
                raise HTTPException(409, "no approved rows to send")
            wrong_cur = [r["row_id"] for r in approved if r["currency"] not in SEND_CURRENCIES]
            if wrong_cur:
                raise HTTPException(409, f"only {sorted(SEND_CURRENCIES)} can be sent; hold or fix rows {wrong_cur}")
            sbid = sender_batch_id(batch_id, [(r["row_id"], r["receiver_email"], money(r["amount"]), r["currency"])
                                              for r in approved])
            p = c.execute("SELECT * FROM payouts WHERE batch_id=?", (batch_id,)).fetchone()
            if p is not None:
                if p["sender_batch_id"] != sbid:  # cannot happen while decisions are locked
                    raise HTTPException(500, "approved set changed after the payout started")
                if p["payout_batch_id"]:
                    return payout_view(c, batch_id)  # already created: no second PayPal call
            else:
                c.execute("INSERT INTO payouts (batch_id, sender_batch_id, created_at) VALUES (?,?,?)",
                          (batch_id, sbid, _now()))
                for r in approved:
                    c.execute("INSERT INTO payout_items (batch_id, row_id, sender_item_id) VALUES (?,?,?)",
                              (batch_id, r["row_id"], r["sender_item_id"]))
            items = [dict(recipient_type="EMAIL", receiver=r["receiver_email"], sender_item_id=r["sender_item_id"],
                          amount=dict(value=money(r["amount"]), currency=r["currency"]),
                          **({"note": r["note"]} if r["note"] else {}))
                     for r in approved]
            audit(c, batch_id, "payout_request", dict(sender_batch_id=sbid, items=items))
            c.commit()  # sender_batch_id is durable BEFORE the PayPal call
            try:
                resp = paypal().create_batch(sbid, items, email_subject="You have a payout")
            except PayPalError as e:
                c.execute("UPDATE payouts SET last_error_json=? WHERE batch_id=?", (json.dumps(e.to_dict()), batch_id))
                audit(c, batch_id, "payout_error", e.to_dict())
                c.commit()
                raise HTTPException(502, dict(message="PayPal rejected the payout", paypal=e.to_dict()))
            hdr = resp["batch_header"]
            c.execute("UPDATE payouts SET payout_batch_id=?, batch_status=?, last_error_json=NULL WHERE batch_id=?",
                      (hdr["payout_batch_id"], hdr.get("batch_status"), batch_id))
            audit(c, batch_id, "payout_response", resp)
            _apply_items(c, batch_id, resp)
            return payout_view(c, batch_id)

    def _apply_items(c, batch_id, resp):
        by_item = {i["sender_item_id"]: i for i in
                   c.execute("SELECT * FROM payout_items WHERE batch_id=?", (batch_id,))}
        for it in resp.get("items", []):
            sid = it.get("payout_item", {}).get("sender_item_id")
            if sid not in by_item:
                raise HTTPException(502, f"PayPal returned unknown sender_item_id {sid!r}")
            old = by_item[sid]
            new_status, errs = it.get("transaction_status"), it.get("errors")
            if new_status != old["transaction_status"] or it.get("payout_item_id") != old["payout_item_id"]:
                c.execute("UPDATE payout_items SET payout_item_id=?, transaction_status=?, errors_json=? "
                          "WHERE batch_id=? AND row_id=?",
                          (it.get("payout_item_id"), new_status, json.dumps(errs) if errs else None, batch_id, old["row_id"]))
                audit(c, batch_id, "status_change", {"row_id": old["row_id"], "sender_item_id": sid,
                                                     "from": old["transaction_status"], "to": new_status, "errors": errs})

    @app.get("/api/batches/{batch_id}/payout")
    def get_payout(batch_id: str):
        with db() as c:
            view = payout_view(c, batch_id)
            if not view["payout_batch_id"]:
                raise HTTPException(409, dict(message="payout not created yet; POST /send again to retry with the same ids",
                                              last_error=view["last_error"]))
            needs = not view["terminal"] or any(i["transaction_status"] in REFRESH_ITEM_STATUSES for i in view["items"])
            if needs:
                try:
                    resp = paypal().get_batch(view["payout_batch_id"])
                except PayPalError as e:
                    raise HTTPException(502, dict(message="PayPal status call failed", paypal=e.to_dict()))
                hdr = resp["batch_header"]
                total = resp.get("total_items")
                if total is not None and total > len(resp.get("items", [])):
                    raise HTTPException(502, f"PayPal reports {total} items but returned {len(resp.get('items', []))}; paging not implemented")
                if hdr.get("batch_status") != view["batch_status"]:
                    c.execute("UPDATE payouts SET batch_status=? WHERE batch_id=?", (hdr.get("batch_status"), batch_id))
                    audit(c, batch_id, "status_change", {"batch_status_from": view["batch_status"], "to": hdr.get("batch_status")})
                _apply_items(c, batch_id, resp)
                view = payout_view(c, batch_id)
            return view

    @app.get("/api/batches/{batch_id}/audit")
    def get_audit(batch_id: str):
        with db() as c:
            batch_view(c, batch_id)
            return [dict(id=e["id"], ts=e["ts"], kind=e["kind"], payload=json.loads(e["payload_json"]))
                    for e in c.execute("SELECT * FROM events WHERE batch_id=? ORDER BY id", (batch_id,))]

    @app.get("/api/eval")
    def get_eval():
        d = _artifacts() / "eval"
        files = sorted(d.glob("*.json"))
        if not files:
            raise HTTPException(500, f"no eval JSON in {d}")
        return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in files}

    return app
