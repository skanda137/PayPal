# PayoutGuard: demo video script

Target length 2:50, hard limit 3:00. Voice-over only, no music. Record the browser at 1920×1080, zoom 100%.

## Before recording

1. `python demo/make_live_subset.py`, then start the backend: `uvicorn backend.app:create_app --factory --port 8000`.
2. Open http://127.0.0.1:8000 in a clean browser window. Use a fresh database (delete `payoutguard.db` first) so
   no old batches exist.
3. Have these four files ready in one folder view: `demo/seeded_batch.csv`, `demo/seeded_history.csv`,
   `demo/live_subset_batch.csv`, `demo/live_seeded_history.csv`.
4. TODO(live-run): do one full practice run of shots 5–7 first. Lines marked TODO(live-run) below must be rewritten
   to match what the sandbox actually showed.
5. The live subset shows the real sandbox email addresses on screen. They are sandbox accounts, not credentials.
   Never show `.env`, the terminal with environment variables, or the PayPal dashboard's secret.

## Shot list

| # | Time | On screen: exact actions | Voice-over |
|---|---|---|---|
| 1 | 0:00–0:15 | **Upload** tab, nothing selected. | "A bulk payout is one CSV and one click. A mistyped email, a payee paid twice, or a slipped decimal goes out with it. PayoutGuard is a review gate between the CSV and PayPal Payouts." |
| 2 | 0:15–0:35 | Choose `seeded_batch.csv` as the batch and `seeded_history.csv` as the history. Click the upload button. The **Review** tab opens. Pause on the chips: 212 rows, 206 proceed, 2 review, 4 hold. | "This is a synthetic batch of 212 payouts with four planted mistakes. The model compares every row with the batch and the payout history. 206 rows pass. Six are stopped for a person to look at." |
| 3 | 0:35–1:05 | Set **Action** to `hold`. Click row **r116**: the panel shows "Possible email typo" and the linked history rows. Click **r070**: amount flag. Click **r122**: duplicate, with r129 linked. | "This email is one character away from a payee we have paid four times. This amount is ten times what this payee normally gets. And this row is an exact repeat. Each flag says why, in plain words, and links the rows it matched." |
| 4 | 1:05–1:25 | Click the **Evaluation** tab. Hover the PR curve, then scroll to **Limitations**. | "All our data is synthetic, and the page says so. On it, the matcher beats a fuzzy-matching baseline, 0.99 average precision against 0.77. It is weak on a payee whose name and email both change: recall drops to 0.72 when that pattern is held out." |
| 5 | 1:25–1:55 | **Upload** tab. Choose `live_subset_batch.csv` and `live_seeded_history.csv`, upload. Review shows 7 rows: 1 proceed, 2 review, 4 hold. Click **Approve** on **r116**. The "Override the model's hold?" dialog opens. Pause, then confirm. | "Now a live run in the PayPal sandbox, with seven rows going to real sandbox accounts. One row starts approved. Flagged rows are never sent unless someone approves them. To show what the model prevents, I override the hold on the mistyped email. That needs a second confirmation." |
| 6 | 1:55–2:30 | Click **Send approved (2)**. The dialog shows 2 rows, total 63.84 USD. Confirm. The **Payout** tab opens and polls. Wait for the statuses. TODO(live-run): cut the wait if it is longer than 15 s; note the real time to final. | "The server sends only the approved rows, as one Payouts batch with an idempotent batch id, so a retry or a second click cannot pay twice. TODO(live-run): describe the statuses actually shown, for example 'The correct payee's payout succeeded. The mistyped one is unclaimed: the money left, and nobody can receive it.' Say this only if that is what the screen shows." |
| 7 | 2:30–2:45 | Click the **Audit log** tab. Scroll to the `override`, `payout_request`, `payout_response` and `status_change` events. Expand `override`. | "Every step is in an append-only log: the upload, the scores, each decision, the override, what we sent to PayPal and what came back." |
| 8 | 2:45–2:55 | Back to the **Review** tab (decisions now locked). | "The model only advises. Deterministic code moves the money, sandbox only, and only what a person approved. That's PayoutGuard." |

## Numbers you may say, and their limits

- 212 rows, 206 proceed, 2 review, 4 hold: pinned by a backend test on the committed files.
- On the 212-row Review screen, the chip "Money at risk (flagged)" shows **585.62 USD**. That is the sum of the six
  flagged rows' amounts. It is not the same number as below; do not mix them.
- **498.29 USD (SYNTHETIC)** is what the four incidents would have paid wrongly, from `demo/money_at_risk.json`.
  If you say it, say "on this synthetic batch".
- 0.99 against 0.77 average precision, and 0.72 held-out recall: from `ml/README.md`, synthetic data.
- Shot 6 total: r163 (22.64) + r116 (41.20) = 63.84 USD.

## Do not say

- Any PayPal behaviour that was not on screen in the recording.
- "Fraud". The product finds payout mistakes; the explanation text avoids that word too.
- Real-world accuracy or savings. Every number is from synthetic data.
