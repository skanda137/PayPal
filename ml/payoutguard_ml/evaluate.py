"""Train + evaluate everything and write the JSON files the UI renders.

Split: 50/20/30 train/calib/test by entity cluster. Pools are disjoint, so test negatives come only
from the test pool (30% of all records): confusable-pair density is lower than in one big pool, and
calibration is tied to that pool size. Stated in summary.json.
"""
from __future__ import annotations
import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, precision_recall_curve
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from . import SEED, __version__
from .anomaly import AmountAnomalyModel
from .baseline import KINDS, all_pairs_matrix
from .blocking import candidate_pairs
from .features import FEATURE_NAMES, feature_matrix
from .matcher import PairMatcher
from .records import RecordTable, build_records, split_of

CUR = "B2026-10"
GRID = np.round(np.arange(0.0, 1.0001, 0.01), 2)
ER_QUEUE_GRID = [0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95, 0.99]
Z_GRID = [2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0, 10.0]


def r4(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), 4)


def wilson(k, n, z=1.96):
    if n == 0:
        return [None, None]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [r4(max(0, c - h)), r4(min(1, c + h))]


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = 2 * p * r / (p + r) if p and r else 0.0
    return p, r, f


# ------------------------------------------------------------------ data prep
def load(data_dir: Path, seed: int):
    led = pd.read_csv(data_dir / "ledger.csv", dtype={"amount": float})
    lab = pd.read_csv(data_dir / "labels" / "row_labels.csv").fillna("")
    ent = pd.read_csv(data_dir / "labels" / "entity_labels.csv")
    rec = build_records(led, CUR)
    row_ent = dict(zip(lab.row_id, lab.entity_id))
    ent_of = []
    for ids in rec.row_ids:
        es = {row_ent[i] for i in ids}
        ent_of.append(sorted(es)[0] if len(es) == 1 else "AMBIGUOUS:" + "|".join(sorted(es)))
    rec["entity_id"] = ent_of
    assert not rec.entity_id.str.startswith("AMBIGUOUS").any(), "record spans several entities"
    rec["cluster_id"] = rec.entity_id.map(dict(zip(ent.entity_id, ent.cluster_id)))
    rec["split"] = [split_of(c, seed) for c in rec.cluster_id]
    return led, lab, ent, rec


def prep(rec: pd.DataFrame, split: str):
    sub = rec[rec.split == split].reset_index(drop=True)
    tbl = RecordTable(sub)
    pairs, bstats = candidate_pairs(tbl)
    X = feature_matrix(tbl, pairs)
    ent = sub.entity_id.values
    y = (ent[pairs[:, 0]] == ent[pairs[:, 1]]).astype(int) if len(pairs) else np.zeros(0, int)
    return sub, tbl, pairs, X, y, bstats


def n_true_pairs(ent: np.ndarray) -> int:
    _, c = np.unique(ent, return_counts=True)
    return int((c * (c - 1) // 2).sum())


# ------------------------------------------------------------------ pair-level curves
def curve_from_scores(scores, y, total_pos):
    """PR points on GRID for candidate scores; positives missing from candidates count as FN."""
    order = np.argsort(-scores, kind="stable")
    s, yy = scores[order], y[order]
    ctp = np.cumsum(yy)
    pts = []
    for t in GRID:
        k = int(np.searchsorted(-s, -t, side="right")) if t > 0 else len(s)
        tp = int(ctp[k - 1]) if k else 0
        fp, fn = k - tp, total_pos - tp
        p, r, f = prf(tp, fp, fn)
        pts.append(dict(threshold=float(t), precision=r4(p), recall=r4(r), f1=r4(f), tp=tp, fp=fp, fn=int(fn)))
    ap = float(average_precision_score(y, scores) * (y.sum() / total_pos)) if y.sum() else 0.0
    best = max(pts, key=lambda d: d["f1"])
    return dict(points=pts, average_precision=r4(ap), best_f1=best["f1"], best_f1_threshold=best["threshold"])


def ece_bins(p, y, n_bins=10, adaptive=False):
    if adaptive:
        edges = np.unique(np.quantile(p, np.linspace(0, 1, n_bins + 1)))
    else:
        edges = np.linspace(0, 1, n_bins + 1)
    idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, len(edges) - 2)
    bins, ece, mce = [], 0.0, 0.0
    for b in range(len(edges) - 1):
        m = idx == b
        if not m.any():
            bins.append(dict(lo=r4(edges[b]), hi=r4(edges[b + 1]), n=0, mean_predicted=None, observed_rate=None))
            continue
        mp, orate = float(p[m].mean()), float(y[m].mean())
        ece += m.mean() * abs(mp - orate)
        mce = max(mce, abs(mp - orate))
        bins.append(dict(lo=r4(edges[b]), hi=r4(edges[b + 1]), n=int(m.sum()), mean_predicted=r4(mp),
                         observed_rate=r4(orate), n_pos=int(y[m].sum())))
    return bins, float(ece), float(mce)


def calib_block(p, y):
    ub, ece, mce = ece_bins(p, y)
    ab, aece, _ = ece_bins(p, y, adaptive=True)
    return dict(uniform_bins=ub, adaptive_bins=ab, ece=r4(ece), ece_adaptive=r4(aece), max_calibration_error=r4(mce),
                brier=r4(brier_score_loss(y, p)), log_loss=r4(log_loss(y, np.clip(p, 1e-6, 1 - 1e-6), labels=[0, 1])))


# ------------------------------------------------------------------ review queue
def er_queue(tbl_sub, pairs, prob, rec_sub, lab_cur, ent_total_cur_pairs, row_ids_cur, taus):
    ent = rec_sub.entity_id.values
    in_cur = rec_sub.in_current.values
    n_hist = rec_sub.n_hist_rows.values
    cur_rows = rec_sub.cur_row_ids.values
    n_rows = len(row_ids_cur)
    involves = in_cur[pairs[:, 0]] | in_cur[pairs[:, 1]]
    is_true = ent[pairs[:, 0]] == ent[pairs[:, 1]]
    inc = []  # incidents: (type, variant_record_idx, entity)
    rid2rec = {r: i for i, ids in enumerate(rec_sub.row_ids.values) for r in ids}
    for _, r in lab_cur[lab_cur.error_types.isin(["alias_duplicate", "email_typo"])].iterrows():
        if r.row_id in rid2rec:
            inc.append((r.error_types, rid2rec[r.row_id], r.entity_id))
    out = []
    for tau in taus:
        sel = involves & (prob >= tau)
        pp = pairs[sel]
        tp = int(is_true[sel].sum())
        fp = int(sel.sum()) - tp
        fn = ent_total_cur_pairs - tp
        p, r, _ = prf(tp, fp, fn)
        rows_flagged, rows_true, rows_new = set(), set(), set()
        partners: dict[int, set] = {}
        for (a, b), t in zip(pp, is_true[sel]):
            for x, y_ in ((a, b), (b, a)):
                if in_cur[x]:
                    rows_flagged.update(cur_rows[x])
                    if n_hist[x] == 0:
                        rows_new.update(cur_rows[x])
                    if t:
                        rows_true.update(cur_rows[x])
                partners.setdefault(int(x), set()).add(int(y_))
        rec_by_type = {}
        for typ in ("alias_duplicate", "email_typo"):
            items = [(v, e) for t_, v, e in inc if t_ == typ]
            hit = sum(1 for v, e in items if any(ent[q] == e for q in partners.get(v, ())))
            rec_by_type[typ] = dict(n=len(items), caught=hit, recall=r4(hit / len(items)) if items else None,
                                    recall_ci95=wilson(hit, len(items)))
        out.append(dict(threshold=tau, flagged_pairs=int(sel.sum()), flagged_rows=len(rows_flagged),
                        flagged_rows_per_1000=r4(1000 * len(rows_flagged) / n_rows),
                        flagged_rows_new_record_per_1000=r4(1000 * len(rows_new) / n_rows),
                        pair_precision=r4(p), pair_recall=r4(r),
                        row_precision=r4(len(rows_true) / len(rows_flagged)) if rows_flagged else None,
                        incident_recall=rec_by_type, flagged_row_ids=sorted(rows_flagged)))
    return out


def exact_dup_rule(cur_df: pd.DataFrame) -> set:
    g = cur_df.groupby(["payee_name", "payee_email", "amount"]).row_id.agg(list)
    return {r for ids in g if len(ids) > 1 for r in ids}


# ------------------------------------------------------------------ anomaly
def anomaly_eval(led, lab, rec, split_by_key, model_fit_keys, seed):
    hist = led[led.batch_id != CUR]
    cur = led[led.batch_id == CUR].merge(lab[["row_id", "entity_id", "error_types"]], on="row_id")
    cur["key"] = [f"{' '.join(str(n).lower().split())}|{str(e).strip().lower()}" for n, e in zip(cur.payee_name, cur.payee_email)]
    hist_by_key = hist.assign(key=[f"{' '.join(str(n).lower().split())}|{str(e).strip().lower()}" for n, e in zip(hist.payee_name, hist.payee_email)]) \
        .groupby("key").amount.agg(list).to_dict()
    cur_of = dict(zip(rec.key, rec.currency))
    sp = rec.set_index("key").split
    cur["split"] = cur.key.map(sp)
    fit_hist = {k: v for k, v in hist_by_key.items() if sp.get(k) == "train"}
    model = AmountAnomalyModel().fit(fit_hist, cur_of)
    pooled = np.concatenate([np.asarray(v) for v in fit_hist.values()])
    g_mu, g_sd = pooled.mean(), pooled.std()
    res = {}
    for s in ("train", "calib", "test"):
        d = cur[cur.split == s].copy()
        sc, ratio_b, glob_b = [], [], []
        for _, r in d.iterrows():
            h = hist_by_key.get(r.key, [])
            ev = model.score(r.amount, h, r.currency)
            sc.append(ev["score"])
            ratio_b.append(abs(np.log(r.amount / np.median(h))) if len(h) >= 3 else 0.0)
            glob_b.append(abs(r.amount - g_mu) / g_sd)
        d["ours"], d["ratio_rule"], d["global_z"] = sc, ratio_b, glob_b
        d["y"] = d.error_types.str.contains("amount_outlier").astype(int)
        res[s] = d
    # threshold: best F1 over Z_GRID on train+calib current rows (the model is fit on history only, never on
    # these rows or their labels; test rows are untouched). Using both splits gives ~4x more positives than calib alone.
    c = pd.concat([res["train"], res["calib"]])
    best = max(Z_GRID, key=lambda z: prf(int(((c.ours >= z) & (c.y == 1)).sum()), int(((c.ours >= z) & (c.y == 0)).sum()),
                                         int(((c.ours < z) & (c.y == 1)).sum()))[2])
    model.threshold = float(best)
    return model, res["test"], c


def pr_downsample(y, s, max_pts=80):
    p, r, t = precision_recall_curve(y, s)
    idx = np.unique(np.linspace(0, len(t) - 1, min(max_pts, len(t))).astype(int)) if len(t) else []
    return [dict(threshold=r4(t[i]), precision=r4(p[i]), recall=r4(r[i])) for i in idx]


def anomaly_sweep(d, scorecol, grid, lab_cur_types):
    n = len(d)
    out = []
    for z in grid:
        sel = d[scorecol] >= z
        tp = int((sel & (d.y == 1)).sum())
        fp = int((sel & (d.y == 0)).sum())
        fn = int((~sel & (d.y == 1)).sum())
        p, r, f = prf(tp, fp, fn)
        out.append(dict(threshold=z, flagged_rows=int(sel.sum()), flagged_rows_per_1000=r4(1000 * sel.sum() / n),
                        precision=r4(p), recall=r4(r), f1=r4(f), tp=tp, fp=fp, fn=fn))
    return out


def recall_at_rate(d, col, rate_per_1000):
    k = int(round(rate_per_1000 * len(d) / 1000))
    top = d.sort_values(col, ascending=False).head(k)
    return r4(top.y.sum() / max(d.y.sum(), 1))


# ------------------------------------------------------------------ main
def run(data_dir: str | Path, art_dir: str | Path, seed: int = SEED) -> dict:
    t0 = time.time()
    data_dir, art_dir = Path(data_dir), Path(art_dir)
    (art_dir / "eval").mkdir(parents=True, exist_ok=True)
    (art_dir / "models").mkdir(parents=True, exist_ok=True)
    led, lab, ent, rec = load(data_dir, seed)
    man = json.loads((data_dir / "labels" / "manifest.json").read_text())

    sub = {s: prep(rec, s) for s in ("train", "calib", "test")}
    _, _, _, Xtr, ytr, _ = sub["train"]
    _, _, _, Xca, yca, _ = sub["calib"]
    rsub, tbl, pairs, Xte, yte, bstats = sub["test"]

    m = PairMatcher(seed).fit(Xtr, ytr)
    cal_info = m.calibrate(Xca, yca)
    oof = getattr(m, "oof_calibrated", m.predict_proba(Xca))
    ca_pts = curve_from_scores(oof, yca, int(yca.sum()))["points"]
    m.threshold = float(max(ca_pts, key=lambda d: d["f1"])["threshold"])
    m.save(art_dir / "models" / "matcher.joblib")

    p_raw, p_cal = m.raw(Xte), m.predict_proba(Xte)
    total_pos = n_true_pairs(rsub.entity_id.values)
    matcher_curve = curve_from_scores(p_cal, yte, total_pos)
    raw_curve = curve_from_scores(p_raw, yte, total_pos)
    # baseline: logistic regression on the same train-split pair features, scored on the same test candidates
    lr = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000)).fit(Xtr, ytr)
    logreg_curve = curve_from_scores(lr.predict_proba(Xte)[:, 1], yte, total_pos)
    mp = next(p for p in matcher_curve["points"] if abs(p["threshold"] - round(m.threshold, 2)) < 1e-9)
    k_match = mp["tp"] + mp["fp"]          # number of pairs the matcher flags at its operating point
    blocking = dict(bstats, true_pairs_total=total_pos, true_pairs_in_candidates=int(yte.sum()),
                    pair_completeness=r4(yte.sum() / total_pos))

    # baselines: all pairs among test records
    n = tbl.n
    iu = np.triu_indices(n, 1)
    e = rsub.entity_id.values
    ytrue = (e[iu[0]] == e[iu[1]])
    ti = np.where(ytrue)[0]
    base_hit, base_same_queue = {}, {}
    base_curves = {}
    for kind in KINDS:
        s = all_pairs_matrix(tbl, kind)[iu]
        order = np.argsort(-s, kind="stable")
        ss, yy = s[order], ytrue[order]
        ctp = np.cumsum(yy)
        pts = []
        for t in GRID:
            k = int(np.searchsorted(-ss, -t + 1e-9, side="right"))
            tp = int(ctp[k - 1]) if k else 0
            p, r, f = prf(tp, k - tp, total_pos - tp)
            pts.append(dict(threshold=float(t), precision=r4(p), recall=r4(r), f1=r4(f), tp=tp, fp=k - tp, fn=int(total_pos - tp)))
        best = max(pts, key=lambda d: d["f1"])
        thr_q = ss[max(k_match - 1, 0)]       # same number of flagged pairs as the matcher (ties may add a few)
        base_hit[kind] = (s[ti] >= thr_q)
        tp_q = int((s >= thr_q).sum())
        base_q = dict(threshold_for_same_queue=r4(thr_q), flagged_pairs=tp_q,
                      precision=r4(int(ytrue[s >= thr_q].sum()) / tp_q), recall=r4(int(ytrue[s >= thr_q].sum()) / total_pos))
        base_same_queue[kind] = base_q
        base_curves[kind] = dict(points=pts, average_precision=r4(average_precision_score(ytrue, s)),
                                 best_f1=best["f1"], best_f1_threshold=best["threshold"])
        del s

    # --- recall by alias kind at each method's operating point
    kinds = {}
    from .normalize import record_key
    key_kind = {}
    rid_key = {rid: k for k, ids in zip(rsub.key, rsub.row_ids) for rid in ids}
    for _, r in lab[(lab.alias_kind != "") & lab.row_id.isin(rid_key)].iterrows():
        key_kind[rid_key[r.row_id]] = r.alias_kind
    for _, r in ent[ent.has_history_alias].iterrows():
        key_kind.setdefault(record_key(r.history_alias_name, r.history_alias_email), "hist:" + str(r.history_alias_kind))
    kind_of = [key_kind.get(k, "") for k in rsub.key]
    ta, tb = iu[0][ti], iu[1][ti]
    def pk(a, b):
        ka, kb = kind_of[a], kind_of[b]
        return ka or kb if not (ka and kb) else "multiple_aliases"
    EMAIL_KINDS = {"email_change", "email_variant", "email_typo"}
    def coarse(k_):
        k_ = k_.replace("hist:", "")
        if k_ in ("multiple_aliases", "unknown"):
            return k_
        if "+" in k_:
            return "name+email"
        return "email_only:" + k_ if k_ in EMAIL_KINDS else "name_only:" + k_
    pair_kind = [pk(a, b) or "unknown" for a, b in zip(ta, tb)]
    pair_coarse = [coarse(k_) for k_ in pair_kind]
    cand = {(int(a), int(b)): float(p) for (a, b), p in zip(pairs, p_cal)}
    m_hit = np.array([cand.get((int(a), int(b)), 0.0) >= m.threshold for a, b in zip(ta, tb)])
    by_kind = {}
    for kd in sorted(set(pair_coarse)):
        idx = [i for i, k_ in enumerate(pair_coarse) if k_ == kd]
        by_kind[kd] = dict(n_pairs=len(idx), matcher_recall=r4(m_hit[idx].mean()),
                           baseline_name_recall_same_queue=r4(base_hit["name"][idx].mean()),
                           baseline_name_plus_email_recall_same_queue=r4(base_hit["combined"][idx].mean()))
    # --- ablation: name+email features only (no amount / currency context)
    ctx = [FEATURE_NAMES.index(c) for c in ("currency_equal", "amt_logratio")]
    keep = [i for i in range(len(FEATURE_NAMES)) if i not in ctx]
    m_abl = PairMatcher(seed).fit(Xtr[:, keep], ytr)
    ablation = dict(average_precision=r4(average_precision_score(yte, m_abl.raw(Xte[:, keep])) * (yte.sum() / total_pos)),
                    note="same model trained without currency/amount features")

    pr_json = dict(
        description="Pair-level precision/recall on the TEST entity pool. Universe = all record pairs; the matcher is "
                    "scored on blocked candidates (missed true pairs count as false negatives); baselines are scored "
                    "brute-force over all pairs.",
        n_test_records=int(n), n_true_pairs=total_pos, matcher_calibrated=matcher_curve, matcher_raw=raw_curve,
        baseline_name_token_sort=base_curves["name"], baseline_name_plus_email=base_curves["combined"],
        baseline_logreg_same_features=logreg_curve,
        recommended_threshold=m.threshold, baselines_at_matcher_queue_size=base_same_queue, recall_by_alias_kind=by_kind, ablation_no_context_features=ablation)
    (art_dir / "eval" / "pr_curve.json").write_text(json.dumps(pr_json, indent=1))

    # calibration
    cal_json = dict(description="Reliability of pair probabilities on candidate pairs of the TEST pool.",
                    calibration_method=cal_info, n_candidate_pairs=int(len(yte)), n_positive=int(yte.sum()),
                    raw=calib_block(p_raw, yte), calibrated=calib_block(p_cal, yte))
    (art_dir / "eval" / "calibration_bins.json").write_text(json.dumps(cal_json, indent=1))

    # review queue
    cur_ids = led[led.batch_id == CUR].row_id
    lab_cur = lab[lab.row_id.isin(cur_ids)]
    test_cur_rows = [r for ids in rsub.cur_row_ids for r in ids]
    in_cur = rsub.in_current.values
    pos_cur = 0
    for ent_id, grp in rsub.groupby("entity_id"):
        k, kc = len(grp), int(grp.in_current.sum())
        pos_cur += k * (k - 1) // 2 - (k - kc) * (k - kc - 1) // 2
    queue = er_queue(tbl, pairs, p_cal, rsub, lab_cur[lab_cur.row_id.isin(test_cur_rows)], pos_cur, test_cur_rows,
                     sorted(set(ER_QUEUE_GRID + [m.threshold])))

    cur_test = led[led.row_id.isin(test_cur_rows)]
    dup_rows = exact_dup_rule(cur_test)
    inj_dup = set(lab_cur[(lab_cur.error_types == "exact_duplicate") & lab_cur.row_id.isin(test_cur_rows)].row_id) | \
        set(lab_cur[lab_cur.row_id.isin(lab_cur[lab_cur.error_types == "exact_duplicate"].partner_row_id) &
                    lab_cur.row_id.isin(test_cur_rows)].row_id)
    dup_hit = len(dup_rows & inj_dup)
    dup_rule = dict(rule="same payee record + same amount appears more than once in the batch",
                    flagged_rows=len(dup_rows), flagged_rows_per_1000=r4(1000 * len(dup_rows) / len(test_cur_rows)),
                    injected_rows=len(inj_dup), caught=dup_hit,
                    recall=r4(dup_hit / len(inj_dup)) if inj_dup else None,
                    precision=r4(dup_hit / len(dup_rows)) if dup_rows else None,
                    note="Deterministic rule. Synthetic data contains no legitimate same-amount repeats, so precision is optimistic by construction.")

    # anomaly
    amodel, atest, acalib = anomaly_eval(led, lab, rec, None, None, seed)
    amodel_path = art_dir / "models" / "anomaly.joblib"
    import joblib
    joblib.dump(amodel, amodel_path)
    a_ours = anomaly_sweep(atest, "ours", Z_GRID, None)
    rngb = np.random.default_rng(seed)
    diffs = []
    for _ in range(300):
        ix = rngb.integers(0, len(atest), len(atest))
        b = atest.iloc[ix]
        if b.y.sum() == 0:
            continue
        diffs.append(average_precision_score(b.y, b.ours) - average_precision_score(b.y, b.ratio_rule))
    ap_diff = dict(mean=r4(np.mean(diffs)), ci95=[r4(np.percentile(diffs, 2.5)), r4(np.percentile(diffs, 97.5))], n_boot=len(diffs),
                   note="per-payee model minus ratio-to-median rule; interval containing 0 means no reliable difference")
    n_out = int(atest.y.sum())
    anomaly_json = dict(
        description="Amount anomaly detection on TEST-pool current-batch rows. Positives = injected amount_outlier rows.",
        n_rows=int(len(atest)), n_positive=n_out, default_threshold=amodel.threshold,
        pooled_sigma=r4(amodel.sigma0), threshold_chosen_on="train+calib current rows (max F1); model itself fit on history only",
        pr_auc_diff_vs_ratio_rule_bootstrap=ap_diff, n_threshold_positives=int(acalib.y.sum()),
        pr_auc=dict(per_payee_model=r4(average_precision_score(atest.y, atest.ours)),
                    ratio_to_median_rule=r4(average_precision_score(atest.y, atest.ratio_rule)),
                    global_zscore=r4(average_precision_score(atest.y, atest.global_z))),
        recall_at_flag_rate={f"{k}_per_1000": dict(per_payee_model=recall_at_rate(atest, "ours", k),
                                                   ratio_to_median_rule=recall_at_rate(atest, "ratio_rule", k),
                                                   global_zscore=recall_at_rate(atest, "global_z", k)) for k in (5, 10, 20, 40)},
        curve=dict(per_payee_model=pr_downsample(atest.y, atest.ours), ratio_to_median_rule=pr_downsample(atest.y, atest.ratio_rule),
                   global_zscore=pr_downsample(atest.y, atest.global_z)),
        sweep=a_ours)
    (art_dir / "eval" / "anomaly_curve.json").write_text(json.dumps(anomaly_json, indent=1))

    # combined queue at ER thresholds with default anomaly threshold + dup rule
    an_rows = set(atest[atest.ours >= amodel.threshold].row_id)
    combined = []
    inc_rows = {}
    for _, r in lab_cur[lab_cur.row_id.isin(test_cur_rows)].iterrows():
        if r.error_types:
            inc_rows[r.row_id] = r.error_types
    for q in queue:
        rows = set(q["flagged_row_ids"]) | an_rows | dup_rows
        combined.append(dict(er_threshold=q["threshold"], anomaly_threshold=amodel.threshold, flagged_rows=len(rows),
                             flagged_rows_per_1000=r4(1000 * len(rows) / len(test_cur_rows)),
                             rows_from_identity=len(q["flagged_row_ids"]), rows_from_amount=len(an_rows), rows_from_exact_dup=len(dup_rows)))
    for q in queue:
        q.pop("flagged_row_ids")
    rq_json = dict(
        description="Review-queue size versus threshold on the TEST pool current batch. flagged_rows counts rows on BOTH "
                    "sides of a flagged pair. Pair precision/recall are against same-entity ground truth.",
        n_current_rows=len(test_cur_rows), recommended_er_threshold=m.threshold,
        identity_matcher=queue, exact_duplicate_rule=dup_rule,
        amount_anomaly=dict(default_threshold=amodel.threshold, sweep=a_ours), combined=combined)
    (art_dir / "eval" / "review_queue.json").write_text(json.dumps(rq_json, indent=1))

    # permutation importance (AP drop on test candidates), top 12
    rng = np.random.default_rng(seed)
    base_ap = average_precision_score(yte, p_raw)
    imp = []
    for j, name in enumerate(FEATURE_NAMES):
        drops = []
        for _ in range(3):
            Xp = Xte.copy()
            Xp[:, j] = rng.permutation(Xp[:, j])
            drops.append(base_ap - average_precision_score(yte, m.raw(Xp)))
        imp.append((name, float(np.mean(drops))))
    imp.sort(key=lambda t: -t[1])

    rec_at = next(q for q in queue if q["threshold"] == m.threshold)
    summary = dict(
        generated_by=f"payoutguard_ml {__version__}", seed=seed, synthetic_data=True, elapsed_seconds=round(time.time() - t0, 1),
        dataset=dict(n_rows=man["n_rows"], n_current_rows=man["n_current_rows"], n_entities=man["n_entities_actual"],
                     n_distinct_records=man["n_distinct_records"], ledger_sha256=man["sha256"]["ledger"],
                     injected_counts_current_batch=man["injected_counts_current_batch"],
                     split_records={s: int(len(sub[s][0])) for s in sub},
                     split_pairs={s: dict(candidates=int(len(sub[s][2])), positives=int(sub[s][4].sum())) for s in sub}),
        blocking=blocking, calibration_method=cal_info,
        identity_matcher=dict(recommended_threshold=m.threshold, threshold_chosen_on="calibration split (out-of-fold calibrated scores, max F1)",
                              at_threshold=dict(precision=mp["precision"], recall=mp["recall"], f1=mp["f1"], flagged_rows_per_1000=rec_at["flagged_rows_per_1000"],
                                                flagged_rows_new_record_per_1000=rec_at["flagged_rows_new_record_per_1000"],
                                                incident_recall=rec_at["incident_recall"]),
                              average_precision=dict(matcher=matcher_curve["average_precision"], baseline_name=base_curves["name"]["average_precision"],
                                                     baseline_name_plus_email=base_curves["combined"]["average_precision"],
                                                     baseline_logreg_same_features=logreg_curve["average_precision"]),
                              best_f1=dict(matcher=matcher_curve["best_f1"], baseline_name=base_curves["name"]["best_f1"],
                                           baseline_name_plus_email=base_curves["combined"]["best_f1"]),
                              baselines_at_matcher_queue_size=base_same_queue, recall_by_alias_kind=by_kind, ablation_no_context_features=ablation,
                              ece=dict(raw=cal_json["raw"]["ece"], calibrated=cal_json["calibrated"]["ece"]),
                              brier=dict(raw=cal_json["raw"]["brier"], calibrated=cal_json["calibrated"]["brier"]),
                              top_features_by_permutation_ap_drop=[dict(feature=a, ap_drop=r4(b)) for a, b in imp[:12]]),
        exact_duplicate_rule=dup_rule,
        amount_anomaly=dict(default_threshold=amodel.threshold, n_positive_test=n_out, pr_auc=anomaly_json["pr_auc"], pr_auc_diff_vs_ratio_rule_bootstrap=ap_diff,
                            at_default=next(x for x in a_ours if x["threshold"] == amodel.threshold),
                            recall_at_flag_rate=anomaly_json["recall_at_flag_rate"]),
        caveats=[
            "All data is synthetic. Numbers measure recovery of the error patterns in generator.py, not real-world accuracy.",
            "Generator and features share design assumptions (name/email edit patterns); nickname table is partially shared (see names.py).",
            "Test pool is 30% of records; negatives are fewer than in a full pool, so precision is optimistic and probabilities are pool-size dependent.",
            "Incident counts are small; see recall_ci95 (Wilson). Do not over-read differences of a few points.",
            "Baselines are brute-force over all pairs; the matcher pays for blocking misses (pair_completeness).",
            "Homonyms (same name, different people) are labelled different; they cap achievable precision."])
    (art_dir / "eval" / "summary.json").write_text(json.dumps(summary, indent=1))
    return summary
