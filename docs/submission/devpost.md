# PayoutGuard: Devpost submission draft

Draft from repository facts only. Every `TODO(live-run)` must be replaced with what the sandbox actually returned,
or the sentence removed, before submitting. Repo: https://github.com/skanda137/PayPal

## Tagline

A review gate for bulk payouts: catch the duplicate payee, the mistyped email and the ×10 amount before the money
leaves.

## What it does

Teams that pay many people at once upload a CSV to PayPal Payouts. A payout sent to a mistyped email, a payee paid
twice under two records, or an amount with a slipped decimal is hard to pull back once it is sent.

PayoutGuard sits between the CSV and PayPal:

1. **Validate.** The CSV must have exactly the 8 agreed columns. If any row is invalid, the whole upload is rejected
   with per-row errors.
2. **Score.** Each row is checked against the rest of the batch and the payout history. Rows get one of three
   actions: `proceed`, `review` or `hold`, with a plain-language reason and links to the rows it matched.
3. **Review.** An operator approves or holds rows in a grid. Rows the model flagged start undecided and are never
   sent unless approved. Approving a row the model held needs an explicit override, which is logged.
4. **Send.** The backend sends only the approved rows as one PayPal Payouts batch, in the sandbox.
5. **Track and audit.** It polls each item's status and records every upload, score, decision, override, request,
   response and status change in an append-only log.

On our seeded demo batch (212 synthetic rows, 4 planted incidents), the 4 incidents would have paid out
**498.29 USD wrongly (SYNTHETIC)**. The model holds 3 and sends the 4th to review, so none is sent without an
operator decision. This is a curated batch, not a real-world estimate.

## How we use PayPal Payouts

- OAuth client-credentials token, then `POST /v1/payments/payouts` to create one batch from the approved rows, and
  `GET /v1/payments/payouts/{id}` to track batch and item status. Sandbox only: the client refuses any other base
  URL.
- **Idempotency.** The `sender_batch_id` is a hash of the batch id and the approved rows. It is saved before the
  PayPal call and also sent as `PayPal-Request-Id`. Timeouts and 5xx responses are retried up to 3 times with the
  same ids, and pressing Send twice never creates a second batch.
- **Money.** Amounts are validated and sent as decimal strings. No float math touches an amount that is paid.
- **Item statuses.** The Payout screen shows each item's `transaction_status` with PayPal's error text.
- TODO(live-run): one sentence on the observed live result: batch status, seconds to final, and the status of the
  mistyped-email item.
- TODO(live-run): what PayPal returned for a reused `sender_batch_id`. Our handling of that rejection is built from
  PayPal's documentation and tested against mocks only.

## How we use AI

- **A trained classifier** scores pairs of payee records: gradient boosting on 27 pairwise features (name, email,
  name-versus-email, currency and amount context), with calibrated probabilities. It finds the same payee under a
  different name or email, and emails one edit away from a known payee.
- **A per-payee amount model** flags amounts far outside a payee's own history.
- **A rule layer** turns scores into actions: an exact repeated row, a never-paid record that matches a known payee
  at probability 0.90 or more, or an amount that looks like a decimal shift is held; any other flag goes to review.
- **Explanations are templates** over the evidence (for example "same name, email one edit away, one record never
  paid"), not generated text.
- **The AI is advisory only.** It never moves money. The payment path is deterministic code, and the server sends
  only rows whose stored decision is `approve`.

On the synthetic test set, the matcher's average precision is 0.993, against 0.775 for a fuzzy name-and-email
baseline. Logistic regression on the same features reaches 0.987, so most of the gain comes from the features, not
the model class.

## AI tools used

- In the product: the scikit-learn classifier and amount model described above, with a rule layer. No LLM is called
  at run time.
- In development: Claude Code (Anthropic) audited the repository and wrote the backend, the front end, the demo
  scripts and the documentation, with human review.
- TODO(team): how the ML package (`ml/`) was written, and with which tools.

## How to run it

Python 3.12+ and Node 20.19+. From the repo root:

```bash
python -m venv .venv            # then activate it
pip install -r requirements.txt
cp .env.example .env            # Windows: copy .env.example .env
cd frontend && npm ci && npm run build && cd ..
uvicorn backend.app:create_app --factory --port 8000
```

Open http://127.0.0.1:8000 and upload `demo/seeded_batch.csv` with `demo/seeded_history.csv` as history. Review,
override, audit log and evaluation all work without a PayPal account. A live send needs your own sandbox app
credentials and sandbox Personal account emails; our sandbox accounts are not shared. Tests:
`python -m pytest -q backend` (26 tests, PayPal mocked).

## Built with

Python, FastAPI, SQLite, scikit-learn, rapidfuzz, pandas, React, Vite, AG Grid, Recharts, PayPal Payouts API
(sandbox).

## Limitations

- **All data is synthetic.** The metrics measure how well the model recovers error patterns that our own generator
  injects. They are not real-world accuracy.
- **Weak on a combined name-and-email change.** When that pattern is held out of training, recall falls to 0.72,
  and to 0.26 on the harder synthetic set. Unseen email typos fall to 0.10 on the harder set.
- **`currency_equal` is a country proxy.** It is the second most important feature, and the generator derives
  currency from country.
- **The review queue is large.** About 196 rows per 1,000 are flagged at the operating threshold on the synthetic
  test batch. About 81 of those involve a record never paid before; the rest are payees who legitimately have two
  records.
- **Small counts.** Incident recall rests on 21 to 49 cases; read the confidence intervals in `ml/README.md`.
- **UNCLAIMED is unconfirmed.** We expect a payout to an unregistered email to come back `UNCLAIMED`.
  TODO(live-run): confirm or correct this.
- USD only. Status updates by polling, no webhooks. Batches with more than one page of items are refused.

## What's next

Webhooks for status changes, paging for large batches, other currencies once tested in the sandbox, and evaluation
on real payout data.
