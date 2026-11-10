# PayoutGuard: demo video script

Target length 2:56, hard limit 3:00. Voice-over only, no music. Record the browser at 1920×1080, zoom 100%.

PayPal results quoted here were observed in the sandbox on 2026-11-10: one run, USD only. Say "in one sandbox run",
never "PayPal always".

## Before recording

1. Rebuild the live files and start the backend:
   `python demo/make_live_from_accounts.py`, `python demo/make_live_subset.py`, then
   `uvicorn backend.app:create_app --factory --port 8000`.
2. Open http://127.0.0.1:8000 in a clean browser window. Use a fresh database (delete `payoutguard.db` first) so
   no old batches exist.
3. Have these four files ready in one folder view: `demo/seeded_batch.csv`, `demo/seeded_history.csv`,
   `demo/live_subset_batch.csv`, `demo/live_seeded_history.csv`.
4. For shot 5, open a terminal in the repo root. Do not rerun `payouts_smoke_test.py`: it overwrites
   `smoke_report.json` and creates new batches. Run `python check_batches.py` once beforehand to confirm it still
   shows the lines named in shot 5. It prints sandbox receiver emails; that is fine, they are sandbox accounts.
5. Do one full practice run of shots 6 and 7 first, then delete `payoutguard.db` again. Check that the screen
   matches the voice-over in shot 7; if it does not, say what the screen shows.
6. Never show `.env`, a terminal with environment variables, or the PayPal dashboard's secret.

## Shot list

| # | Time | On screen: exact actions | Voice-over |
|---|---|---|---|
| 1 | 0:00–0:14 | **Upload** tab, nothing selected. | "A bulk payout is one CSV and one click. A mistyped email, a payee paid twice, or a slipped decimal goes out with it. PayoutGuard is a review gate between the CSV and PayPal Payouts." |
| 2 | 0:14–0:32 | Choose `seeded_batch.csv` as the batch and `seeded_history.csv` as the history. Click the upload button. The **Review** tab opens, flagged rows first. Pause on the chips: 212 rows, 206 proceed, 2 review, 4 hold. | "This is a synthetic batch of 212 payouts with four planted mistakes. The model compares every row with the batch and the payout history. 206 rows pass. Six are stopped for a person to look at." |
| 3 | 0:32–0:57 | The side panel is already open on r070. Click **r116**: "Possible email typo" with the linked history rows. Click **r122**: duplicate, with r129 linked. | "This amount is ten times what this payee normally gets. This email is one character away from a payee we have paid four times. And this row is an exact repeat. Each flag says why, in plain words, and links the rows it matched." |
| 4 | 0:57–1:12 | Click the **Evaluation** tab. Hover the PR curve, then scroll to **Limitations**. | "All our data is synthetic, and the page says so. The matcher beats a fuzzy baseline, 0.99 average precision against 0.77, but it is weak on a payee whose name and email both change: recall 0.72." |
| 5 | 1:12–1:27 | Terminal: run `python check_batches.py`. Scroll to the batch with `dup-1` (twice) and the batch with `semdup-1` and `semdup-2`. Hold on `dup-1 ... FAILED DUPLICATE_ITEM`, then on `semdup-1` and `semdup-2` both `SUCCESS`. | "We tested PayPal's own duplicate check in the sandbox. The same item id twice: the second item fails, yet the batch still says SUCCESS. The same person under two different item ids: both SUCCESS. Paid twice, no warning. That is one sandbox run." |
| 6 | 1:27–1:52 | **Upload** tab. Choose `live_subset_batch.csv` and `live_seeded_history.csv`, upload. Review shows 9 rows: 3 proceed, 2 review, 4 hold. Tick the five flagged rows other than r116 (r070, r073, r122, r129, r144) and click **Hold selected (5)**. Click r116 and press **A**: the "Override the model's hold?" dialog opens. Pause, then confirm. | "Now a live run in the PayPal sandbox. Three payouts go to registered accounts and start approved. The six rows the model flagged need a decision, and the send button stays off until they have one. I hold five. To show what the model prevents, I override the hold on the mistyped email, which needs a second confirmation." |
| 7 | 1:52–2:27 | Click **Send approved (4)**. The confirm panel shows 4 items, 78.68 USD, 5 rows not sent (544.42 USD), and the override in red. Confirm. The **Payout** tab opens and polls. Wait for the statuses; cut the wait if it is longer than 15 s. | "The confirm panel lists what is sent, what is not, and the override in red. The server sends only approved rows, as one Payouts batch with an idempotent id, so a retry or a second click cannot pay twice. Three payouts are delivered. The mistyped email is unclaimed, and the batch still says SUCCESS, which is why we judge every item, not the batch." Say the last two sentences only if the screen shows three `SUCCESS` items and r116 `UNCLAIMED`; otherwise describe what it shows. |
| 8 | 2:27–2:44 | Click the **Audit log** tab. Click the `override` row, which opens its payload. Scroll past `payout_request`, `payout_response` and the `status_change` events. | "Every step is in an append-only log: the upload, the scores, each decision, the override, what we sent to PayPal and what came back." |
| 9 | 2:44–2:56 | Back to the **Review** tab (decisions now locked). | "The model only advises. Deterministic code moves the money, sandbox only, and only what a person approved. That's PayoutGuard." |

## Numbers you may say, and their limits

- 212 rows, 206 proceed, 2 review, 4 hold: pinned by a backend test on the committed files.
- On the 212-row Review screen, the chip "Money at risk (flagged)" shows **585.62 USD**. That is the sum of the six
  flagged rows' amounts. It is not the same number as below; do not mix them.
- **498.29 USD (SYNTHETIC)** is what the four incidents would have paid wrongly, from `demo/money_at_risk.json`.
  If you say it, say "on this synthetic batch".
- 0.99 against 0.77 average precision, and 0.72 held-out recall: from `ml/README.md`, synthetic data.
- Live subset: 9 rows, 623.10 USD. Three start approved: r016 (7.20), r028 (7.64) and r163 (22.64), 37.48 USD.
- Shot 7: those three plus the overridden r116 (41.20) = 4 items, 78.68 USD. Not sent: r070 (462.20), r073 (36.31),
  r122 (4.80), r129 (4.80), r144 (36.31) = 544.42 USD.
- Shot 5, observed 2026-11-10 in one sandbox run (USD only): same `sender_item_id` twice gave a paid first item and a
  `FAILED` / `DUPLICATE_ITEM` second item inside a `SUCCESS` batch; the same receiver under two item ids gave two
  `SUCCESS` items; a mistyped email gave `UNCLAIMED` / `RECEIVER_UNREGISTERED`; a replayed `sender_batch_id` gave
  HTTP 400.
- Shot 7: the three registered accounts delivered `SUCCESS` in the smoke test. The r116 row goes to a placeholder
  address, not a registered account, so `UNCLAIMED` is expected from the smoke test's mistyped-email result.

## Do not say

- Any PayPal behaviour that was not on screen in the recording or listed above.
- "Fraud". The product finds payout mistakes; the explanation text avoids that word too.
- Real-world accuracy or savings. Every number is from synthetic data.
- That unclaimed funds come back after 30 days, as if observed. That is PayPal's documentation; we did not observe it.
