"""Backend tests. No live PayPal calls: the app gets a fake client, the client gets a fake HTTP session.
Run from the repo root:  python -m pytest -q backend"""
import csv
import io
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient  # noqa: E402

from backend.app import COLUMNS, create_app  # noqa: E402
from backend.payouts import SANDBOX_BASE_URL, PayPalClient, PayPalError, money, sender_batch_id  # noqa: E402
from payoutguard_ml import score_batch  # noqa: E402  (path added by backend.app)

DEMO = Path(__file__).resolve().parent.parent / "ml" / "artifacts" / "demo" / "data"


# ---------------------------------------------------------------- helpers

def to_csv(rows) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode()


def row(rid, name, email, amount, currency="USD"):
    return dict(row_id=rid, sender_item_id=f"si-{rid}", receiver_email=email, recipient_name=name, amount=amount,
                currency=currency, note="Monthly payout", date="2026-11-01")


class FakePayPal:
    """Behaves like PayPal for dedupe: the same sender_batch_id returns the same batch."""

    def __init__(self):
        self.creates, self.by_sbid, self.batches = [], {}, {}

    def create_batch(self, sbid, items, email_subject):
        self.creates.append(dict(sbid=sbid, items=items))
        if sbid not in self.by_sbid:
            pid = f"PB{len(self.by_sbid) + 1:04d}"
            self.by_sbid[sbid] = pid
            self.batches[pid] = dict(sbid=sbid, items=items)
        return {"batch_header": {"payout_batch_id": self.by_sbid[sbid], "batch_status": "PENDING"}}

    def get_batch(self, pid):
        b = self.batches[pid]
        return {"batch_header": {"payout_batch_id": pid, "batch_status": "SUCCESS",
                                 "sender_batch_header": {"sender_batch_id": b["sbid"]}},
                "total_items": len(b["items"]),
                "items": [{"payout_item_id": f"I{i}", "transaction_status": "UNCLAIMED" if i == 0 else "SUCCESS",
                           "payout_item": {"sender_item_id": it["sender_item_id"]},
                           **({"errors": {"name": "RECEIVER_UNREGISTERED", "message": "Receiver is unregistered"}} if i == 0 else {})}
                          for i, it in enumerate(b["items"])]}


@pytest.fixture
def env(tmp_path):
    fake = FakePayPal()
    db = tmp_path / "pg.db"
    return TestClient(create_app(str(db), paypal_client=fake)), fake, db


def upload(client, rows, history=None):
    files = {"file": ("batch.csv", to_csv(rows), "text/csv")}
    if history is not None:
        files["history"] = ("history.csv", to_csv(history), "text/csv")
    return client.post("/api/batches", files=files)


def decided(client, rows):
    """Upload, then hold every flagged row that starts undecided, so the batch may be sent."""
    b = upload(client, rows).json()
    undecided = {r["row_id"]: "hold" for r in b["rows"] if r["decision"] is None}
    return client.put(f"/api/batches/{b['batch_id']}/decisions", json=undecided).json()


SMALL = [row("r1", "Alice Moreno", "alice.moreno@example.com", "120.00"),
         row("r2", "Bilal Okafor", "bilal.okafor@example.org", "75.50"),
         row("r3", "Chen Wei", "chen.wei@example.net", "300"),
         row("r4", "Chen Wei", "chen.wei@example.net", "300")]   # exact repeat of r3 -> hold


# ---------------------------------------------------------------- validation

def test_validation_rejects_bad_rows_and_scores_nothing(env):
    client, _, db = env
    bad = [row("a", "A Person", "a@example.com", "12.345"),
           row("b", "B Person", "b@example.com", "0"),
           row("c", "C Person", "c@example.com", "-5"),
           row("d", "D Person", "not-an-email", "10"),
           row("e", "E Person", "e@example.com", "10", currency="usd"),
           {**row("f", "F Person", "f@example.com", "10"), "date": "11/01/2026"},
           row("a", "A Again", "a2@example.com", "1.00")]
    r = upload(client, bad)
    assert r.status_code == 422
    errs = {(e["row_id"], e["field"]) for e in r.json()["detail"]["errors"]}
    assert {("a", "amount"), ("b", "amount"), ("c", "amount"), ("d", "receiver_email"), ("e", "currency"),
            ("f", "date"), ("a", "row_id")} <= errs
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM rows").fetchone()[0] == 0


def test_validation_rejects_missing_and_unknown_columns(env):
    client, _, _ = env
    r = client.post("/api/batches", files={"file": ("b.csv", b"row_id,receiver_email,country\nr1,a@b.com,US\n", "text/csv")})
    assert r.status_code == 422
    msg = r.json()["detail"]["errors"][0]["message"]
    assert "missing columns" in msg and "unexpected columns ['country']" in msg


def test_money_is_two_decimal_string_without_float():
    assert money("300") == "300.00" and money("75.5") == "75.50" and money("0.10") == "0.10"
    with pytest.raises(ValueError):
        money("12.345")


# ---------------------------------------------------------------- gate

def test_gate_only_approved_rows_reach_payouts(env):
    client, fake, _ = env
    b = upload(client, SMALL).json()
    rows = {r["row_id"]: r for r in b["rows"]}
    assert rows["r3"]["action"] == rows["r4"]["action"] == "hold"
    assert rows["r3"]["decision"] is None and rows["r4"]["decision"] is None      # hold starts undecided
    assert rows["r1"]["action"] == "proceed" and rows["r1"]["decision"] == "approve"
    client.put(f"/api/batches/{b['batch_id']}/decisions", json={"r2": "hold", "r3": "hold", "r4": "hold"}).raise_for_status()

    r = client.post(f"/api/batches/{b['batch_id']}/send")
    assert r.status_code == 200, r.text
    assert len(fake.creates) == 1
    sent = [it["sender_item_id"] for it in fake.creates[0]["items"]]
    assert sent == ["si-r1"]                                                    # held r2, r3, r4 never sent
    assert fake.creates[0]["items"][0]["amount"] == {"value": "120.00", "currency": "USD"}


def test_send_is_refused_while_a_flagged_row_is_undecided(env):
    client, fake, _ = env
    b = upload(client, SMALL).json()                         # r3 and r4 are held by the model and undecided
    bid = b["batch_id"]
    r = client.post(f"/api/batches/{bid}/send")
    assert r.status_code == 409 and r.json()["detail"]["row_ids"] == ["r3", "r4"]
    assert "undecided" in r.json()["detail"]["message"]
    client.put(f"/api/batches/{bid}/decisions", json={"r3": "hold"}).raise_for_status()
    r = client.post(f"/api/batches/{bid}/send")
    assert r.status_code == 409 and r.json()["detail"]["row_ids"] == ["r4"]
    assert fake.creates == []
    assert client.get(f"/api/batches/{bid}").json()["summary"]["locked"] is False       # nothing written or locked
    assert "payout_request" not in [e["kind"] for e in client.get(f"/api/batches/{bid}/audit").json()]
    client.put(f"/api/batches/{bid}/decisions", json={"r4": "hold"}).raise_for_status()
    assert client.post(f"/api/batches/{bid}/send").status_code == 200
    assert [it["sender_item_id"] for it in fake.creates[0]["items"]] == ["si-r1", "si-r2"]


def test_no_approved_rows_is_refused(env):
    client, fake, _ = env
    b = upload(client, SMALL).json()
    client.put(f"/api/batches/{b['batch_id']}/decisions", json={r: "hold" for r in ("r1", "r2", "r3", "r4")}).raise_for_status()
    r = client.post(f"/api/batches/{b['batch_id']}/send")
    assert r.status_code == 409 and r.json()["detail"] == "no approved rows to send"
    assert fake.creates == []


def test_decisions_lock_after_send(env):
    client, _, _ = env
    b = decided(client, SMALL)
    client.post(f"/api/batches/{b['batch_id']}/send").raise_for_status()
    r = client.put(f"/api/batches/{b['batch_id']}/decisions", json={"r3": "approve"})
    assert r.status_code == 409


def test_exact_duplicate_holds_every_occurrence(env):
    client, _, _ = env
    b = upload(client, SMALL).json()
    rows = {r["row_id"]: r for r in b["rows"]}
    for rid in ("r3", "r4"):                     # first occurrence is held too, not silently passed
        assert rows[rid]["action"] == "hold" and rows[rid]["decision"] is None
        assert [f["type"] for f in rows[rid]["flags"]] == ["duplicate_payee"]


SEEDED = Path(__file__).resolve().parent.parent / "demo"


def test_committed_seeded_demo_flags_exactly_the_seeded_rows(env):
    """demo/seeded_*.csv (placeholder emails) must flag the same 6 rows as the local live version."""
    client, _, _ = env
    files = {"file": ("seeded_batch.csv", (SEEDED / "seeded_batch.csv").read_bytes(), "text/csv"),
             "history": ("seeded_history.csv", (SEEDED / "seeded_history.csv").read_bytes(), "text/csv")}
    r = client.post("/api/batches", files=files)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["summary"]["n_rows"] == 212 and b["summary"]["n_history"] == 599
    flagged = {x["row_id"]: (x["action"], [f["type"] for f in x["flags"]]) for x in b["rows"] if x["action"] != "proceed" or x["flags"]}
    assert flagged == {
        "r070": ("hold", ["amount_anomaly"]),
        "r073": ("review", ["duplicate_payee"]),
        "r116": ("hold", ["typo_receiver"]),
        "r122": ("hold", ["duplicate_payee"]),
        "r129": ("hold", ["duplicate_payee"]),
        "r144": ("review", ["duplicate_payee"]),
    }


def test_approving_hold_row_requires_override_and_is_audited(env):
    client, fake, _ = env
    b = upload(client, SMALL).json()
    bid = b["batch_id"]
    r = client.put(f"/api/batches/{bid}/decisions", json={"r3": "approve"})
    assert r.status_code == 409 and r.json()["detail"]["row_ids"] == ["r3"]
    assert {x["row_id"]: x["decision"] for x in client.get(f"/api/batches/{bid}").json()["rows"]}["r3"] is None
    client.put(f"/api/batches/{bid}/decisions?override=true", json={"r3": "approve"}).raise_for_status()
    ev = [e for e in client.get(f"/api/batches/{bid}/audit").json() if e["kind"] == "override"]
    assert len(ev) == 1 and ev[0]["payload"]["row_id"] == "r3" and ev[0]["payload"]["flag_types"] == ["duplicate_payee"]
    client.put(f"/api/batches/{bid}/decisions", json={"r4": "hold"}).raise_for_status()
    client.post(f"/api/batches/{bid}/send").raise_for_status()
    assert sorted(it["sender_item_id"] for it in fake.creates[0]["items"]) == ["si-r1", "si-r2", "si-r3"]


def test_bad_decision_values_and_unknown_rows(env):
    client, _, _ = env
    b = upload(client, SMALL).json()
    assert client.put(f"/api/batches/{b['batch_id']}/decisions", json={"r1": "yes"}).status_code == 422
    assert client.put(f"/api/batches/{b['batch_id']}/decisions", json={"zz": "hold"}).status_code == 422


def test_built_front_end_is_served_at_root_and_api_still_works(tmp_path, monkeypatch):
    import backend.app as app_module
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>PayoutGuard</title>", encoding="utf-8")
    monkeypatch.setattr(app_module, "UI_DIST", dist)
    client = TestClient(create_app(str(tmp_path / "pg.db"), paypal_client=FakePayPal()))
    assert "PayoutGuard" in client.get("/").text
    assert client.get("/api/eval").status_code == 200
    assert client.get("/api/batches/nope").status_code == 404

    monkeypatch.setattr(app_module, "UI_DIST", tmp_path / "missing")
    client = TestClient(create_app(str(tmp_path / "pg2.db"), paypal_client=FakePayPal()))
    assert client.get("/").status_code == 404


def test_send_with_non_sandbox_base_url_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("PAYPAL_BASE_URL", "https://api-m.paypal.com")
    client = TestClient(create_app(str(tmp_path / "pg.db")))
    b = upload(client, SMALL).json()
    r = client.post(f"/api/batches/{b['batch_id']}/send")
    assert r.status_code == 500 and "sandbox only" in r.json()["detail"]
    assert client.get(f"/api/batches/{b['batch_id']}").json()["summary"]["locked"] is False


def test_send_without_credentials_fails_before_locking(tmp_path, monkeypatch):
    for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET"):
        monkeypatch.setenv(k, "")          # empty, so a real .env (if present) is not loaded over it
    monkeypatch.setenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
    client = TestClient(create_app(str(tmp_path / "pg.db")))
    b = upload(client, SMALL).json()
    r = client.post(f"/api/batches/{b['batch_id']}/send")
    assert r.status_code == 500 and "PAYPAL_CLIENT_ID" in r.json()["detail"]
    assert client.get(f"/api/batches/{b['batch_id']}").json()["summary"]["locked"] is False
    assert client.put(f"/api/batches/{b['batch_id']}/decisions", json={"r2": "hold"}).status_code == 200
    assert [e["kind"] for e in client.get(f"/api/batches/{b['batch_id']}/audit").json()] == ["upload", "score", "decision"]


def test_non_usd_approved_row_blocks_send(env):
    client, fake, _ = env
    b = upload(client, [row("r1", "Alice Moreno", "alice.moreno@example.com", "10.00", currency="GBP")]).json()
    r = client.post(f"/api/batches/{b['batch_id']}/send")
    assert r.status_code == 409 and "r1" in r.json()["detail"]
    assert fake.creates == []


# ---------------------------------------------------------------- idempotency (app level)

def test_second_send_creates_no_second_batch(env):
    client, fake, _ = env
    b = decided(client, SMALL)
    first = client.post(f"/api/batches/{b['batch_id']}/send").json()
    second = client.post(f"/api/batches/{b['batch_id']}/send").json()
    assert len(fake.creates) == 1 and len(fake.by_sbid) == 1
    assert first["payout_batch_id"] == second["payout_batch_id"]
    assert first["sender_batch_id"].startswith("pg-") and len(first["sender_batch_id"]) == 27


def test_failed_create_is_retried_with_same_sender_batch_id(env):
    client, fake, _ = env

    calls = []

    def flaky(sbid, items, email_subject):
        calls.append(sbid)
        if len(calls) == 1:
            raise PayPalError(None, "NETWORK_ERROR", "timed out after retries", None)
        return FakePayPal.create_batch(fake, sbid, items, email_subject)

    fake.create_batch = flaky
    b = decided(client, SMALL)
    assert client.post(f"/api/batches/{b['batch_id']}/send").status_code == 502
    assert client.get(f"/api/batches/{b['batch_id']}/payout").status_code == 409
    assert client.post(f"/api/batches/{b['batch_id']}/send").status_code == 200
    assert calls[0] == calls[1] and len(fake.by_sbid) == 1


def test_sender_batch_id_is_deterministic_and_order_independent():
    a = [("r1", "a@x.com", "1.00", "USD"), ("r2", "b@x.com", "2.00", "USD")]
    assert sender_batch_id("b1", a) == sender_batch_id("b1", list(reversed(a)))
    assert sender_batch_id("b1", a) != sender_batch_id("b1", [a[0], ("r2", "b@x.com", "2.01", "USD")])
    assert sender_batch_id("b1", a) != sender_batch_id("b2", a)


# ---------------------------------------------------------------- status + audit

def test_status_poll_stores_item_statuses_and_audit_is_append_only(env):
    client, _, db = env
    b = decided(client, SMALL)
    client.post(f"/api/batches/{b['batch_id']}/send").raise_for_status()
    p = client.get(f"/api/batches/{b['batch_id']}/payout").json()
    assert p["batch_status"] == "SUCCESS" and p["terminal"]
    st = {i["row_id"]: i for i in p["items"]}
    assert st["r1"]["transaction_status"] == "UNCLAIMED" and st["r1"]["errors"]["name"] == "RECEIVER_UNREGISTERED"
    assert st["r2"]["transaction_status"] == "SUCCESS"
    kinds = [e["kind"] for e in client.get(f"/api/batches/{b['batch_id']}/audit").json()]
    assert kinds[:2] == ["upload", "score"]
    assert {"payout_request", "payout_response", "status_change"} <= set(kinds)
    with sqlite3.connect(db) as c:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            c.execute("DELETE FROM events")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            c.execute("UPDATE events SET kind='x'")


def test_failed_item_inside_a_success_batch_is_stored_as_failed(env):
    """Shape observed in the sandbox on 2026-11-10: same sender_item_id twice -> batch SUCCESS, second item FAILED."""
    client, fake, _ = env
    b = decided(client, SMALL)                               # sends r1 and r2
    bid = b["batch_id"]

    def observed(pid):
        items = fake.batches[pid]["items"]
        return {"batch_header": {"payout_batch_id": pid, "batch_status": "SUCCESS", "funding_source": "BALANCE",
                                 "sender_batch_header": {"sender_batch_id": fake.batches[pid]["sbid"]}},
                "items": [
                    {"payout_item_id": "ITEM1", "transaction_id": "TX1", "transaction_status": "SUCCESS",
                     "payout_batch_id": pid, "payout_item": {"sender_item_id": items[0]["sender_item_id"]}},
                    {"payout_item_id": "ITEM2", "transaction_status": "FAILED", "payout_batch_id": pid,
                     "payout_item": {"sender_item_id": items[1]["sender_item_id"]},
                     "errors": {"name": "DUPLICATE_ITEM", "message": "Duplicate payout item", "information_link": "",
                                "details": [], "links": []}}]}

    fake.get_batch = observed
    client.post(f"/api/batches/{bid}/send").raise_for_status()
    p = client.get(f"/api/batches/{bid}/payout").json()
    assert p["batch_status"] == "SUCCESS" and p["terminal"]
    st = {i["row_id"]: i for i in p["items"]}
    assert st["r1"]["transaction_status"] == "SUCCESS" and st["r1"]["errors"] is None
    assert st["r2"]["transaction_status"] == "FAILED" and st["r2"]["errors"]["name"] == "DUPLICATE_ITEM"
    changes = [e["payload"] for e in client.get(f"/api/batches/{bid}/audit").json() if e["kind"] == "status_change"]
    failed = [c for c in changes if c.get("row_id") == "r2"]
    assert failed[-1]["to"] == "FAILED" and failed[-1]["error_name"] == "DUPLICATE_ITEM"
    assert [c for c in changes if c.get("row_id") == "r1"][-1]["error_name"] is None


def test_eval_endpoint_merges_artifacts(env):
    client, _, _ = env
    e = client.get("/api/eval").json()
    assert {"summary", "pr_curve", "calibration_bins", "review_queue", "anomaly_curve", "heldout_alias_kind"} <= set(e)
    assert e["summary"]["synthetic_data"] is True


# ---------------------------------------------------------------- scoring wiring (demo CSV)

def _demo_contract(name):
    df = pd.read_csv(DEMO / name, dtype=str, keep_default_na=False)
    return [dict(row_id=r.row_id, sender_item_id=r.row_id, receiver_email=r.payee_email, recipient_name=r.payee_name,
                 amount=r.amount, currency=r.currency, note=r.note, date=r.batch_date) for r in df.itertuples()]


def test_scoring_wiring_with_demo_csv(env):
    client, _, _ = env
    batch, hist = _demo_contract("current_batch.csv"), _demo_contract("history.csv")
    r = upload(client, batch, hist)
    assert r.status_code == 200, r.text[:500]
    b = r.json()
    to_df = lambda rows: pd.DataFrame(rows, columns=COLUMNS).astype({"amount": float})  # noqa: E731
    direct = score_batch(to_df(batch), to_df(hist))
    assert [x["row_id"] for x in b["rows"]] == [x["row_id"] for x in direct]
    for got, exp in zip(b["rows"], direct):
        assert got["action"] == exp["action"] and got["flags"] == exp["flags"]          # flags passed through unchanged
        assert got["decision"] == ("approve" if exp["action"] == "proceed" else None)
    s = b["summary"]
    assert s["n_rows"] == 287 and s["n_history"] == len(hist) and s["er_threshold"] == 0.45
    assert sum(s["actions"].values()) == 287 and s["actions"]["hold"] > 0 and s["actions"]["review"] > 0
    assert len(s["model_version"]) == 16


# ---------------------------------------------------------------- PayPal client (fake HTTP session)

class FakeResp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.text = "" if body is None else str(body)

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeSession:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def post(self, url, **kw):  # oauth only
        assert url.endswith("/v1/oauth2/token")
        return FakeResp(200, {"access_token": "T", "expires_in": 3600})

    def request(self, method, url, json=None, headers=None, timeout=None):
        self.calls.append(dict(method=method, url=url, json=json, headers=headers))
        nxt = self.script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def client_with(script):
    s = FakeSession(script)
    sleeps = []
    return PayPalClient("id", "secret", SANDBOX_BASE_URL, session=s, sleep=sleeps.append), s, sleeps


ITEMS = [{"recipient_type": "EMAIL", "receiver": "a@x.com", "sender_item_id": "s1",
          "amount": {"value": "1.00", "currency": "USD"}}]


def test_client_retries_5xx_with_same_ids():
    c, s, sleeps = client_with([FakeResp(503, {"name": "SERVICE_UNAVAILABLE"}), FakeResp(500, {"name": "INTERNAL_SERVICE_ERROR"}),
                                FakeResp(201, {"batch_header": {"payout_batch_id": "PB1", "batch_status": "PENDING"}})])
    out = c.create_batch("pg-abc", ITEMS)
    assert out["batch_header"]["payout_batch_id"] == "PB1"
    posts = [x for x in s.calls if x["method"] == "POST"]
    assert len(posts) == 3
    assert {p["headers"]["PayPal-Request-Id"] for p in posts} == {"pg-abc"}
    assert {p["json"]["sender_batch_header"]["sender_batch_id"] for p in posts} == {"pg-abc"}
    assert sleeps == [1, 2]


def test_client_timeout_then_duplicate_rejection_fetches_original():
    dup = FakeResp(400, {"name": "USER_BUSINESS_ERROR", "message": "duplicate sender_batch_id", "debug_id": "d1",
                         "links": [{"href": f"{SANDBOX_BASE_URL}/v1/payments/payouts/PBORIG", "rel": "self", "method": "GET"}]})
    orig = FakeResp(200, {"batch_header": {"payout_batch_id": "PBORIG", "batch_status": "SUCCESS",
                                           "sender_batch_header": {"sender_batch_id": "pg-abc"}}, "items": []})
    c, s, _ = client_with([requests.Timeout("read timeout"), dup, orig])
    out = c.create_batch("pg-abc", ITEMS)
    assert out["batch_header"]["payout_batch_id"] == "PBORIG"
    methods = [x["method"] for x in s.calls]
    assert methods == ["POST", "POST", "GET"]                       # no third create
    assert s.calls[0]["headers"]["PayPal-Request-Id"] == s.calls[1]["headers"]["PayPal-Request-Id"] == "pg-abc"


def test_client_duplicate_rejection_in_the_observed_sandbox_shape_fetches_original():
    """Sandbox, 2026-11-10: HTTP 400 USER_BUSINESS_ERROR, empty top-level links, link under details[0].link."""
    dup = FakeResp(400, {"name": "USER_BUSINESS_ERROR", "message": "User business error.", "debug_id": "d1",
                         "information_link": "https://developer.paypal.com/docs/api/payments.payouts-batch/#errors",
                         "details": [{"field": "SENDER_BATCH_ID", "location": "body",
                                      "issue": "Batch with given sender_batch_id already exists",
                                      "link": [{"href": "https://api.sandbox.paypal.com/v1/payments/payouts/PBORIG",
                                                "rel": "self", "method": "GET", "encType": "application/json"}]}],
                         "links": []})
    orig = FakeResp(200, {"batch_header": {"payout_batch_id": "PBORIG", "batch_status": "SUCCESS",
                                           "sender_batch_header": {"sender_batch_id": "pg-abc"}}, "items": []})
    c, s, _ = client_with([requests.Timeout("read timeout"), dup, orig])
    out = c.create_batch("pg-abc", ITEMS)
    assert out["batch_header"]["payout_batch_id"] == "PBORIG"
    assert [x["method"] for x in s.calls] == ["POST", "POST", "GET"]            # no third create


def test_client_duplicate_link_to_other_batch_raises():
    dup = FakeResp(400, {"name": "USER_BUSINESS_ERROR", "message": "dup",
                         "links": [{"href": f"{SANDBOX_BASE_URL}/v1/payments/payouts/PBOTHER"}]})
    other = FakeResp(200, {"batch_header": {"payout_batch_id": "PBOTHER",
                                            "sender_batch_header": {"sender_batch_id": "pg-zzz"}}})
    c, _, _ = client_with([dup, other])
    with pytest.raises(PayPalError) as e:
        c.create_batch("pg-abc", ITEMS)
    assert e.value.name == "DUPLICATE_LINK_MISMATCH"


def test_client_parses_error_body_and_does_not_retry_4xx():
    c, s, _ = client_with([FakeResp(422, {"name": "VALIDATION_ERROR", "message": "Invalid request", "debug_id": "abc123",
                                          "details": [{"field": "items[0].amount.value", "issue": "INVALID"}]})])
    with pytest.raises(PayPalError) as e:
        c.create_batch("pg-abc", ITEMS)
    assert (e.value.status, e.value.name, e.value.debug_id) == (422, "VALIDATION_ERROR", "abc123")
    assert e.value.details[0]["field"] == "items[0].amount.value"
    assert len(s.calls) == 1


def test_client_gives_up_after_three_retries():
    c, s, _ = client_with([requests.ConnectionError("x")] * 4)
    with pytest.raises(PayPalError) as e:
        c.create_batch("pg-abc", ITEMS)
    assert e.value.name == "NETWORK_ERROR" and len(s.calls) == 4


def test_client_refuses_non_sandbox_base_url():
    with pytest.raises(ValueError, match="sandbox only"):
        PayPalClient("id", "secret", "https://api-m.paypal.com")
