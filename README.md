# PayoutGuard

PayoutGuard checks a CSV of payouts before it is sent. It flags rows that look like a duplicate payee, a mistyped
receiver email, or an unusual amount, and explains why. An operator approves or holds each row. Only then does a
deterministic backend send the approved rows as one PayPal Payouts batch (**sandbox only**), track each item's
status, and record every step in an append-only audit log.

The ML is advisory. It never moves money. The server enforces the approval gate: rows the operator did not approve
are never sent, whatever the UI does.

```
CSV ──► validate ──► score_batch (ml/) ──► review UI ──► approve / hold ──► server gate ──► PayPal Payouts (sandbox)
                                                                                              │
                                     audit log ◄── every step ──────────── item status polling ◄┘
```

## Run it locally

Requires Python 3.12+ and Node 20.19+ (or 22.12+). Run every command from the repo root unless a step says otherwise.

**1. Backend**

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # Windows: copy .env.example .env   then fill in the sandbox credentials
uvicorn backend.app:create_app --factory --port 8000
```

The backend reads `.env` from the repo root and imports the ML package from `ml/` by path, so no extra install
step is needed. It stops with a clear error if `PAYOUTGUARD_DB` is unset. `PAYPAL_*` variables are only needed when
you press Send, and any base URL other than `https://api-m.sandbox.paypal.com` is refused.

**2. Front end** (second terminal)

```bash
cd frontend
npm ci
npm run dev                     # http://localhost:5173, proxies /api to :8000
```

**3. Tests** (no live PayPal calls; PayPal is mocked)

```bash
python -m pytest -q backend     # API, gate, idempotency, PayPal client
cd ml && python -m pytest -q tests
```

## Demo data

| File | What | Send it? |
|---|---|---|
| `demo/demo_batch.csv` + `demo/demo_history.csv` | 155 USD rows + 1,179 history rows from the ML's **synthetic** demo set (`python demo/make_demo_csv.py`) | **No.** About 110 rows start approved, and they go to made-up addresses. Use it to show the review screen |
| `demo/live_batch.csv` + `demo/live_history.csv` | 3 rows: two real sandbox Personal accounts plus one mistyped email, USD 1.00 each. Generated from `.env` by `python demo/make_live_csv.py` (gitignored) | Yes. This is the live sandbox run |

In the live batch, the mistyped row is held by the model (`typo_receiver`). Sending it anyway requires the
operator override, which is recorded in the audit log.

## Screens

- **Upload:** batch CSV plus an optional history CSV. If any row is invalid, the whole upload is rejected with
  per-row errors and nothing is scored.
- **Review:** AG Grid with the model's action, top flag and category, and the operator's decision. Click a row to
  see each flag's explanation and linked rows. Approve or hold rows one at a time or in bulk. Approving a row the
  model held opens a confirmation, and it is logged as an `override`. "Send approved" shows the count and total first.
- **Payout:** polls PayPal every 3 seconds until the batch is terminal, and shows each item's status
  (SUCCESS, UNCLAIMED, FAILED, RETURNED, …) with PayPal's error text.
- **Audit log:** every upload, score, decision, override, payout request and response, and status change.
- **Evaluation:** PR curves against the baselines, a calibration plot, a review-queue threshold slider, the
  held-out check and limitations, all labelled synthetic.

## API

See `docs/openapi.json` (exported from the running app).

| Method | Path | |
|---|---|---|
| POST | `/api/batches` | multipart `file` (+ optional `history`) → `{batch_id, summary, rows}`; 422 with `errors[]` if any row is invalid |
| GET | `/api/batches/{id}` | same shape |
| PUT | `/api/batches/{id}/decisions[?override=true]` | `{row_id: "approve" \| "hold"}`. Approving a `hold` row needs `override=true`. 409 once a payout exists |
| POST | `/api/batches/{id}/send` | sends approved rows only; calling it again never creates a second batch |
| GET | `/api/batches/{id}/payout` | `{batch_status, terminal, items:[{row_id, transaction_status, errors}]}`; refreshes from PayPal while not final |
| GET | `/api/batches/{id}/audit` | append-only event list |
| GET | `/api/eval` | `ml/artifacts/eval/*.json`, merged by file name |

Row shape: `{row_id, recipient_name, receiver_email, amount, currency, action, flags, decision}`. `flags` are the
`score_batch` flags, unchanged. `proceed` rows start approved. `review` and `hold` rows start undecided and are never
sent unless approved.

## How money movement is kept safe

- **Gate:** `/send` reads only rows whose stored decision is `approve`. Decisions lock once a payout starts.
- **Idempotency:** `sender_batch_id = "pg-" + sha256(batch_id + sorted approved (row_id, email, amount, currency))[:24]`.
  It is saved to the database before the PayPal call and also sent as `PayPal-Request-Id`. Timeouts and 5xx are
  retried up to 3 times with the same ids. If PayPal rejects a reused `sender_batch_id`, the client fetches the
  original batch from the link in the rejection, and accepts it only if that batch carries the same
  `sender_batch_id`. It never creates a second batch.
- **Money:** amounts are validated as decimal strings with at most 2 decimals and sent as `"12.50"`. No float math
  touches an amount that is paid.
- **Currency:** only USD rows can be sent for now. Whether sandbox converts other currencies has not been tested.
- **Audit:** SQLite triggers reject UPDATE and DELETE on the events table.

## Limitations

- **All ML data is synthetic.** The metrics measure recovery of the error patterns the generator injects, not
  real-world accuracy. Details: `ml/README.md`.
- **`currency_equal` is a country proxy.** It is the second most important feature, and the generator derives
  currency from country, so it stands in for country even though the scoring contract has no country column.
- **Held-out recall drops.** The matcher does not generalise to an unseen combined name-and-email change
  (held-out recall 0.72; 0.26 in hard mode), or to unseen email typos in hard mode (0.10).
- **Not yet observed live:** the exact PayPal response to a reused `sender_batch_id`, and multi-currency sandbox
  payouts. The client's duplicate handling is built from PayPal's docs and is checked only against mocks.
- Payout status updates only while someone polls (the Payout screen or the API). There are no webhooks.
- Status calls with more than one page of items are refused rather than truncated.

## AI tools used

- Claude Code (Anthropic) audited the repository and wrote the backend (`backend/`), the front end (`frontend/`),
  the demo scripts (`demo/`) and this README, with human review.
- ML package (`ml/`): _team to confirm._

## License

MIT, see `LICENSE`.
