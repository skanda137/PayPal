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

## Quick start for judges

Needs Python 3.12+ and Node 20.19+ (or 22.12+). Run from the repo root. No PayPal account is needed for steps 1–3.

**1. Install and start** (a few minutes)

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                    # Windows: copy .env.example .env
cd frontend && npm ci && npm run build && cd ..
uvicorn backend.app:create_app --factory --port 8000
```

Open **http://127.0.0.1:8000**. One process serves the API and the built front end.

**2. See the review flow without a sandbox**

1. On **Upload**, choose `demo/seeded_batch.csv` as the batch and `demo/seeded_history.csv` as the history.
2. **Review** shows 212 rows: 206 proceed, 2 review, 4 hold. Filter Action = hold, and click r116 (email typo),
   r070 (amount ×10) or r122 (exact duplicate) to read the explanation.
3. Approve a held row: a confirmation opens, and the **Audit log** tab then shows an `override` event.
4. **Evaluation** shows the model's metrics, all on synthetic data.

Do not press Send on this batch. Without credentials it stops with "missing env vars" and nothing is sent or locked.

**3. Run the tests** (PayPal is mocked; no network)

```bash
python -m pytest -q backend             # 29 tests: API, gate, idempotency, PayPal client
cd ml && python -m pytest -q tests      # 17 tests, up to 2 minutes
```

**4. Optional: a live sandbox send.** This needs your own PayPal sandbox app, because our sandbox accounts are not
shared:

- Put your sandbox REST app's Client ID and Secret in `.env`. The app needs Payouts enabled.
- Upload a small USD CSV with the 8 columns (see the API section), using your own sandbox Personal account emails as
  receivers.
- The emails in `demo/seeded_batch.csv` are placeholders, not sandbox accounts, so that batch is for the review
  screen only.

## Verification status

**Verified with automated tests and a browser run, against a mocked PayPal:**

- 29 backend tests and 17 ML tests pass, including in a fresh copy of the committed files that follows the quick
  start above.
- The approval gate, the hold override, decision locking, retries with the same ids, and the audit log's
  append-only rule each have a test.
- The committed seeded batch flags exactly the 6 expected rows (pinned by a test).
- The full UI flow (upload errors, review, override, send, payout status, audit log, evaluation) was run in a
  headless browser against the real backend with a fake PayPal client.

**Observed in the PayPal sandbox** on 2026-11-10: **one run, USD only.** `payouts_smoke_test.py` created four
batches, and `check_batches.py` read the results back afterwards.

| Case | What PayPal did |
|---|---|
| Token request | Succeeded, and the Payouts scope is present. |
| Valid receivers (3 registered sandbox Personal accounts) | Items `SUCCESS`. The batch reached `SUCCESS` 25 seconds after creation. |
| Mistyped receiver email | Item `UNCLAIMED`, error `RECEIVER_UNREGISTERED`. |
| Replayed `sender_batch_id` | HTTP 400 `USER_BUSINESS_ERROR`, "Batch with given sender_batch_id already exists". The link to the original batch is under `details[].link[]`; the top-level `links` is empty. |
| Same `sender_item_id` twice in one batch | Created with HTTP 201. The first item was paid; the second ended `FAILED` with `DUPLICATE_ITEM`. The **batch status still read `SUCCESS`**. |
| Same receiver under two different `sender_item_id`s | Both items `SUCCESS`: **the receiver was paid twice, with no warning.** |

What this means for the product:

- PayPal's duplicate protection works on ids only. An identical `sender_item_id` is caught; the same person under
  different ids is not. Catching that (and name variants, and typo'd emails) is what PayoutGuard adds.
- A `SUCCESS` batch can contain `FAILED` and `UNCLAIMED` items, so the Payout screen judges each item and never
  shows "all paid" from the batch status.
- The client reads the original batch link from `details[].link[]` as well as the top level, because of the shape
  above. Its recovery path (fetch the original batch and check its `sender_batch_id`) is tested against a mock built
  from that exact response; it has not been exercised against the live sandbox.

The backend has also made one live payout (`demo/live_run.py`, before the account file was corrected): the batch was
created through the backend and settled as `SUCCESS`, and its one approved item came back `UNCLAIMED`
(`RECEIVER_UNREGISTERED`), because that email was not a registered account.

**Not yet observed:**

- `demo/live_run.py` with the three registered accounts in `demo/sandbox_accounts.json`. Those rows are expected to
  come back `SUCCESS`, as the same accounts did in the smoke test.
- Non-USD payouts. The backend refuses to send them.

## The live sandbox run (team)

```bash
python demo/preflight.py               # offline checks of .env and the sandbox account file; prints no values
python payouts_smoke_test.py           # direct PayPal sandbox calls; prints a summary, writes smoke_report.json
python demo/make_live_from_accounts.py # local live files = committed placeholders + the accounts in sandbox_accounts.json
python demo/make_live_subset.py        # writes the 9-row live batch
uvicorn backend.app:create_app --factory --port 8000     # second terminal
python demo/live_run.py                # upload, approve the 3 unflagged rows, hold the rest, send, poll, save the audit
python demo/live_run.py --override r116                   # also override the hold on the typo row and send it
```

`demo/sandbox_accounts.json` (gitignored) maps the roles `clean`, `clean2` and `clean3` to three registered
sandbox Personal accounts; use the `RECEIVERS` from `.env`, which delivered `SUCCESS` in the smoke test.
`live_run.py` refuses to start if the backend is not reachable, and refuses batches of more than 10 rows. Its
report goes to `demo/live_run_report.json` (gitignored); `--report PATH` writes it elsewhere, for example when
testing against a fake PayPal.

## Demo data

| File | What | Send it? |
|---|---|---|
| `demo/seeded_batch.csv` + `demo/seeded_history.csv` | 212 USD rows (total 4,061.12) + 599 history rows, all **synthetic**, with 4 seeded incidents. Placeholder emails | **Never send the full batch.** 206 rows start approved (3,475.50 USD), mostly to made-up addresses. Use it to show the review screen |
| `demo/money_at_risk.json` | What the 4 incidents would have paid out with and without PayoutGuard | n/a |
| `demo/live_seeded_*.csv`, `demo/sandbox_accounts.json` | The same batch with three payees (r163, r016, r028) swapped to the registered sandbox Personal accounts. **Local only, gitignored** | No, use the subset |
| `demo/live_subset_batch.csv` | 9 rows, 623.10 USD: the three payouts to registered accounts (r016, r028, r163; 37.48 USD; the only rows that start approved) plus the six flagged incident rows. Made by `python demo/make_live_subset.py` (gitignored) | Yes, with `demo/live_seeded_history.csv` as history. This is the live sandbox run |

The seeded incidents, and what the model does with them (the committed and live versions score identically):

| Row | Incident | Model |
|---|---|---|
| r122 | exact duplicate of r129 (both held) | hold |
| r070 | amount ×10 for this payee | hold |
| r116 | one-character email typo | hold (`typo_receiver`) |
| r073 | same payee as r144 under a reordered name and a new email (both in review) | review |

**SYNTHETIC: 498.29 USD** would have been paid wrongly without the gate, and the model stops all of it (the baseline
in that file stops 461.98). This is seeded incidents on a curated batch, not a real-world estimate.

`python demo/make_live_from_accounts.py` builds the local live files from the committed ones, and
`python demo/make_seeded_placeholders.py` does the reverse. Each swaps emails for the accounts or the placeholders
and writes its files only if every row still gets the same action and flags. In the live subset, only the three
payouts to registered accounts start approved. The six flagged rows go to placeholder addresses, so they are never
sent unless an operator overrides; the override of r116, the typo row, is recorded in the audit log.

## Developing the front end

`npm run dev` in `frontend/` serves the UI on http://localhost:5173 with hot reload and proxies `/api` to the backend
on port 8000. Run `npm run build` again to update what the backend serves at `/`.

The backend reads `.env` from the repo root and imports the ML package from `ml/` by path, so no extra install step
is needed. It stops with a clear error if `PAYOUTGUARD_DB` is unset. `PAYPAL_*` variables are only needed when you
press Send, and any base URL other than `https://api-m.sandbox.paypal.com` is refused.

## Screens

- **Upload:** batch CSV plus an optional history CSV. If any row is invalid, the whole upload is rejected with
  per-row errors and nothing is scored.
- **Review:** AG Grid with the model's action, top flag and category, and the operator's decision. Click a row to
  see each flag's explanation and linked rows. Approve or hold rows one at a time or in bulk. Approving a row the
  model held opens a confirmation, and it is logged as an `override`. "Send approved" shows the count and total first.
- **Payout:** polls every 3 seconds until the batch is final, and shows each item's status with PayPal's error text.
- **Audit log:** every upload, score, decision, override, payout request and response, and status change.
- **Evaluation:** PR curves against the baselines, a calibration plot, a review-queue threshold slider, the
  held-out check and limitations, all labelled synthetic.

## API

See `docs/openapi.json` (exported from the app).

| Method | Path | |
|---|---|---|
| POST | `/api/batches` | multipart `file` (+ optional `history`) → `{batch_id, summary, rows}`; 422 with `errors[]` if any row is invalid |
| GET | `/api/batches/{id}` | same shape |
| PUT | `/api/batches/{id}/decisions[?override=true]` | `{row_id: "approve" \| "hold"}`. Approving a `hold` row needs `override=true`. 409 once a payout exists |
| POST | `/api/batches/{id}/send` | sends approved rows only; calling it again never creates a second batch |
| GET | `/api/batches/{id}/payout` | `{batch_status, terminal, items:[{row_id, transaction_status, errors}]}`; refreshes from PayPal while not final |
| GET | `/api/batches/{id}/audit` | append-only event list |
| GET | `/api/eval` | `ml/artifacts/eval/*.json`, merged by file name |

CSV columns, exactly: `row_id, sender_item_id, receiver_email, recipient_name, amount, currency, note, date`.

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
- **Currency:** only USD rows can be sent for now.
- **Audit:** SQLite triggers reject UPDATE and DELETE on the events table.

## Limitations

- **All ML data is synthetic.** The metrics measure recovery of the error patterns the generator injects, not
  real-world accuracy. Details: `ml/README.md`.
- **`currency_equal` is a country proxy.** It is the second most important feature, and the generator derives
  currency from country, so it stands in for country even though the scoring contract has no country column.
- **Held-out recall drops.** The matcher does not generalise to an unseen combined name-and-email change
  (held-out recall 0.72; 0.26 in hard mode), or to unseen email typos in hard mode (0.10).
- **The review queue is large.** On the synthetic test batch about 196 rows per 1,000 are flagged at the operating
  threshold. About 81 of those involve a record never paid before; the rest are payees who legitimately have two
  records.
- **PayPal behaviour was observed once.** The sandbox results in "Verification status" come from one run, in USD
  only, so treat them as one observation, not a guarantee.
- Payout status updates only while someone polls (the Payout screen or the API). There are no webhooks.
- Status calls with more than one page of items are refused rather than truncated.

## AI tools used

- Claude Code (Anthropic) audited the repository and wrote the backend (`backend/`), the front end (`frontend/`),
  the demo scripts (`demo/`), the submission drafts (`docs/submission/`) and this README, with human review.
- ML package (`ml/`): _team to confirm._

## License

MIT, see `LICENSE`.
