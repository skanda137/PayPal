# PayoutGuard ML

Synthetic ledger generator, rapidfuzz baseline, calibrated pairwise matcher, per-payee amount anomaly model,
evaluation harness (JSON outputs for the UI) and plain-language explanations.

**All data is synthetic. Every metric below measures recovery of the error patterns written in
`generator.py`, not real-world payout accuracy.** The model is advisory only; it never moves money.

## Run

```bash
pip install -r requirements.txt
make all          # generate -> train+evaluate -> hard-mode stress test -> held-out check -> demo findings   (~1.5 min on 2 CPUs)
make test         # 15 tests
```
Single seed (`payoutguard_ml.SEED = 2026`) drives everything: generation, splits (hash of seed + cluster id),
model training, bootstrap. Same seed gives byte-identical `ledger.csv` (SHA-256 in `labels/manifest.json`).

## Team contract: `score_batch`

```python
from payoutguard_ml import score_batch
res = score_batch(batch_df, history_df, mode="model")      # mode="baseline" = rapidfuzz-only, same output shape
```
Both frames use the agreed columns `row_id, sender_item_id, receiver_email, recipient_name, amount, currency, note, date`
(only `row_id, receiver_email, recipient_name, amount, currency` are required). Returns one dict per batch row, in batch order:

```json
{"row_id": "r17", "action": "review",
 "flags": [{"type": "duplicate_payee", "prob": 0.91, "linked_rows": ["r04"],
            "evidence": {"name_sim": 0.88, "email_edit_dist": 1, "signals": ["NAME_INITIAL", "..."]},
            "explanation": "plain-language text built from the evidence"}]}
```
- Flag types: `duplicate_payee`, `typo_receiver` (same name, email one edit away, one record never paid), `amount_anomaly`. One extension: `invalid_input` (bad email, amount <= 0).
- `prob` is the calibrated match probability; it is `null` for `amount_anomaly` and `invalid_input` (a z-score is not a probability; see `evidence.z`). Exact repeated rows are `duplicate_payee` with `prob` 1.0.
- `action`: `hold` = never-paid record matching a known payee at prob >= 0.90, an exact repeat, an invalid row, or an amount that looks like a decimal shift (x/÷10 or more); `review` = any other flag; `proceed` = no flags. In `baseline` mode nothing is held on a probability, because the baseline score is a similarity, not a probability.
- `linked_rows` can point at history rows; if a history `row_id` equals a batch `row_id` it is returned as `history:<row_id>`.
- Only pairs at or above the operating threshold (model 0.31, baseline 0.91) become flags. Use `er_threshold=` to override.
- Artifacts are read from `artifacts/` (override with `PAYOUTGUARD_ARTIFACTS`). Advisory only; sending is the caller's decision.

## Leakage check: leave-one-alias-kind-out (`artifacts/eval/heldout_alias_kind.json`, hard data in `artifacts/hard/eval/`)

For each alias kind, all records of that kind are removed from training and calibration, the matcher is retrained and its threshold re-chosen without
them, and recall is measured on test pairs of that kind. Recall at the held-out operating point (standard data, hard in brackets; n = test pairs):

| Held-out kind | n | Seen in training | Held out |
|---|---|---|---|
| nickname | 3 (1) | 1.00 | 1.00 (1.00) |
| initial | 12 (6) | 1.00 | 1.00 (1.00) |
| reorder | 20 (10) | 1.00 | 0.95 (1.00) |
| name typo | 28 (9) | 1.00 | 1.00 (1.00) |
| middle initial | 17 (6) | 1.00 | 1.00 (1.00) |
| email variant | 65 (16) | 0.98 | 0.97 (1.00) |
| email typo | 23 (30) | 1.00 (0.93) | 0.91 (**0.17**) |
| email change | 57 (21) | 0.98 | 0.93 (0.95) |
| name and email both changed | 58 (182) | 0.93 | **0.67 (0.21)** |

Reading it: the matcher generalises to unseen single-change aliases, so those results are not just memorised patterns. It does **not** generalise to an unseen
combined name-and-email change, nor (in hard mode) to unseen email typos. When the held-out kind is the dominant one (hard mode, 70% combined), little
training signal is left, so that row is a stress test, not a typical error. Dropping the features written for a kind (`heldout_no_dedicated_features`) changed
recall by at most one pair, so those hand-written features are not what carries it. Features were still designed knowing these kinds exist, so this does not prove
independence from the generator. Several cells have n < 30; the file includes Wilson intervals.

## Layout

| Path | What |
|---|---|
| `payoutguard_ml/generator.py` | ledger + labels, error types documented in `ERROR_TYPES` / `ALIAS_KINDS` |
| `records.py`, `normalize.py` | distinct (name,email) records, normalisation, soundex, deterministic split |
| `blocking.py` | phonetic/initial/nickname/email keys, block cap 80; reports pair completeness |
| `features.py` | 28 symmetric pair features (name, email, name-vs-email, country/currency/amount context) |
| `baseline.py` | rapidfuzz: `name` (token_sort_ratio) and `combined` (name + email-local ratio), brute-force all pairs |
| `matcher.py` | HistGradientBoosting + isotonic/Platt (picked by 5-fold CV Brier) |
| `anomaly.py` | per-payee robust log-z, shrunk toward pooled sigma; cohort fallback for new payees |
| `evaluate.py` | trains, evaluates, writes `artifacts/eval/*.json`, `artifacts/models/*.joblib` |
| `explain.py` | evidence -> text templates; constrained optional LLM rewording |
| `score.py` | **team contract** `score_batch(batch_df, history_df)`; `mode="baseline"` for the rapidfuzz-only pipeline |
| `pipeline.py` | richer `Analyzer.analyze(...)` (all candidate pairs, evidence, explanations) that `score_batch` wraps |
| `heldout.py` | leave-one-alias-kind-out leakage check |
| `data/eval`, `data/hard` | generated datasets (ledger, history, current_batch, `labels/`) |
| `artifacts/` | models, `eval/` JSON, `hard/` (stress test), `demo/` (400-entity demo set + `demo_findings.json`) |

## Data and labels

`ledger.csv` columns: `row_id,batch_id,batch_date,payee_name,payee_email,country,amount,currency,note` (no labels).
Six history batches (2026-04..09) and the current batch `B2026-10`. Ground truth lives only in `labels/`:
`row_labels.csv` (entity, injected `error_types`, `partner_row_id`, `alias_kind`, `outlier_factor`),
`entity_labels.csv`, `manifest.json`.

| Error type (injected into the current batch) | Rate | Definition |
|---|---|---|
| `alias_duplicate` | 4% | existing payee appears under a different name and/or email; `both` (canonical row also in batch) or `drift` (only alias row) |
| `email_typo` | 2% | correct name, email with exactly one character edit |
| `exact_duplicate` | 1.5% | row repeated verbatim |
| `amount_outlier` | 3% | x10, x100, /10, /100, or random x3-8 / x0.05-0.3; only for payees with >=3 prior payouts |

Alias kinds: reorder, initial, middle add/drop, nickname, name typo, honorific, legal suffix, email change, email variant (+tag, dots, digits), and combinations.
Also in the data: ~20% of persons / 10% of businesses were legitimately paid under two records in history (true same-entity pairs, not errors);
hard negatives = relatives (same surname + domain), look-alike names (one-edit), homonyms (identical name, different people).
`--hard` (`data/hard`): 70% of aliases change name and email, alias rows lose/alter country (50%) and drift in amount, hard-negative rates doubled.

**Split:** 50/20/30 train/calibration/test by entity cluster (relatives stay together). Pools are disjoint, so test negatives come only from the
test pool (~2,000 records). Calibration is tied to that pool size.

## Results (test pool; 284 true record pairs, ~1,240 current-batch rows)

Identity matching, standard data (hard-mode in brackets):

| | Matcher | Baseline name | Baseline name+email |
|---|---|---|---|
| Average precision (all pairs) | 0.994 (0.972) | 0.651 (0.538) | 0.775 (0.619) |
| Best F1 | 0.971 (0.923) | 0.736 (0.653) | 0.770 (0.661) |
| At the matcher's queue size (same number of flagged pairs): precision / recall | 0.962 / 0.979 (0.869 / 0.953) | 0.680 / 0.711 (0.577 / 0.662) | 0.752 / 0.789 (0.634 / 0.716) |

- Operating threshold 0.31 (hard: 0.30), chosen on out-of-fold calibrated scores of the calibration split, not on test.
- Incident recall at that threshold: `alias_duplicate` 49/49 (Wilson 95% CI 0.93-1.00), `email_typo` 21/21 (0.85-1.00). Hard mode: 0.90 (0.78-0.95) and 1.00 (0.86-1.00).
- Blocking pair completeness 1.00 on test, reduction ratio 0.995. Not a guarantee for real data: every generated alias keeps a name token or the email prefix.
- Ablation without country/currency/amount features: AP 0.950 (hard 0.925). Context features matter, and they assume each payee has a stable country and typical amount.
- Calibration: raw GBM scores are already well calibrated (ECE 0.0004 raw vs 0.0009 after sigmoid; both near zero, differences are noise). Calibration here is a safeguard, not a big gain.
- `flagged_rows_per_1000` is ~199 at the operating threshold, of which ~81 involve a record never paid before (`flagged_rows_new_record_per_1000`).
  The rest are payees who legitimately have two records. Use the new-record number as the actionable queue and show the other as informational.
- Exact-duplicate rule: 42/42 injected rows caught. Precision is optimistic by construction (the data has no legitimate same-amount repeats).

Amount anomaly (44 injected outliers in test, hard 39):

| | Per-payee model | Ratio-to-median rule | Global z-score |
|---|---|---|---|
| PR-AUC | 0.880 (0.914) | 0.781 (0.889) | 0.406 (0.258) |

Bootstrap difference vs the ratio rule: +0.096 (95% CI 0.03 to 0.17) on standard data, +0.023 (-0.03 to 0.07) on hard data, so the advantage is reliable on standard data only.
Default z threshold 8.0 (hard 10.0): about 28 flags per 1000 rows, precision 0.94, recall 0.75. Lower thresholds trade queue size for recall; see `review_queue.json`.

## JSON outputs (`artifacts/eval/`, same files in `artifacts/hard/eval/`)

- `pr_curve.json`: `matcher_calibrated`, `matcher_raw`, `baseline_name_token_sort`, `baseline_name_plus_email`, each `{points:[{threshold,precision,recall,f1,tp,fp,fn}] (101 points), average_precision, best_f1, best_f1_threshold}`; plus `recommended_threshold`, `baselines_at_matcher_queue_size`, `recall_by_alias_kind`, `ablation_no_context_features`. `precision` is `null` when nothing is flagged.
- `calibration_bins.json`: `raw` and `calibrated`, each `{uniform_bins, adaptive_bins:[{lo,hi,n,n_pos,mean_predicted,observed_rate}], ece, ece_adaptive, max_calibration_error, brier, log_loss}`; `calibration_method`.
- `review_queue.json`: `identity_matcher` = list over thresholds `{threshold, flagged_pairs, flagged_rows, flagged_rows_per_1000, flagged_rows_new_record_per_1000, pair_precision, pair_recall, row_precision, incident_recall{alias_duplicate,email_typo:{n,caught,recall,recall_ci95}}}`; `exact_duplicate_rule`; `amount_anomaly.sweep`; `combined` (queue size at each identity threshold plus the default anomaly threshold and the duplicate rule).
- `anomaly_curve.json`: PR curves for the three scorers, `sweep` over z thresholds, `recall_at_flag_rate`, bootstrap difference.
- `summary.json`: headline numbers, dataset/split/blocking stats, top permutation importances, caveats list.

## Backend API

```python
from payoutguard_ml.pipeline import Analyzer
an = Analyzer.load("artifacts")
out = an.analyze(batch_df, history_df, er_threshold=None, z_threshold=None, min_prob=0.2, llm_fn=None)
```
Inputs need `row_id,batch_id,payee_name,payee_email,amount,currency` (`country` optional); `batch_df` holds exactly one `batch_id`.
Output (JSON-serialisable): `{batch_id, n_rows, findings[], summary, disclaimer}`. Each finding has `type`
(`identity_match | exact_duplicate | amount_anomaly | invalid_input`), `severity`, `affected_row_ids`, `recommended_action`
(`hold_for_review | note`), `explanation` (`headline, bullets, caution, text, text_source, facts`), and for identity matches
`probability`, `above_threshold`, `category`, `signals`, `records`. Pairs between `min_prob` and the threshold are returned with
`above_threshold=false` so the UI slider can reveal them without re-running. Duplicate, invalid-input and amount findings are
rules or model scores, not probabilities. Idempotency, approval gating and payment execution belong to the backend.
`artifacts/demo/demo_findings.json` is a ready sample.

## Explanations

Text comes from templates over evidence fields (signals such as `NAME_REORDERED`, `EMAIL_ONE_EDIT`, `ONE_RECORD_NEW`, counts, amounts).
Signals describe what is present in the data, not feature attributions of the model. Optional LLM rewording (`llm_fn`) is accepted only if every
number, email and quoted name already appears in the template text and no banned word (fraud, scam, certainly, ...) is used; otherwise
the template text is returned (`text_source="template"`, reasons in `llm_rejected_reasons`). `LLM_SYSTEM_PROMPT` is in `explain.py`.

## Known limitations

- Synthetic data; generator and features share assumptions (edit patterns, stable country/amount). The nickname table is partly shared
  (`NICKNAMES_FEAT` is a 22-of-35 subset), which limits but does not remove circularity.
- In `--hard` mode country is blanked but `currency` (derived from country in the generator) is not, so `currency_equal` leaks country there.
- Homonyms are labelled different people and cap precision; they are only ~1.5-3% of entities.
- Incident and outlier counts are small (21-49 and 39-44). Read the Wilson/bootstrap intervals, not the point estimates.
- Probabilities are calibrated for a ~2,000-record pool; a larger real pool has more look-alikes and needs recalibration.
- Amounts are not currency-converted. Anomaly thresholds come from a max-F1 pick on 77-82 labelled rows.
