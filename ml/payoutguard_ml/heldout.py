"""Leakage check: leave-one-alias-kind-out.

For each held-out kind, every record of that kind is removed from training and calibration (all pairs
touching it). The matcher is retrained and the threshold re-chosen WITHOUT that kind, then recall is
measured on test pairs of that kind only. A second variant also drops the feature(s) written specifically
for that kind. Features were designed with these kinds in mind, so this shows generalisation of the model,
not independence of the feature set; the second variant is the stricter test.
"""
from __future__ import annotations
import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import SEED
from .evaluate import CUR, GRID, curve_from_scores, load, prep, r4, wilson
from .features import FEATURE_NAMES
from .matcher import PairMatcher
from .normalize import record_key

EMAIL_KINDS = {"email_change", "email_variant", "email_typo"}
# held-out group -> (alias_kind values it covers, features written for it)
GROUPS = {
    "nickname": ({"nickname"}, ["nick_used"]),
    "initial": ({"initial"}, ["initial_compat"]),
    "reorder": ({"reorder"}, ["aligned_jw", "tok_jaccard"]),
    "name_typo": ({"typo_name"}, []),
    "middle_initial": ({"middle_add", "middle_drop"}, []),
    "email_variant": ({"email_variant"}, ["em_canon_equal"]),
    "email_typo": ({"email_typo"}, ["em_lev_le1"]),
    "email_change": ({"email_change"}, []),
    "name_and_email_both": (None, []),       # any combined alias (kind contains '+')
}


def _record_kinds(lab, ent, rec) -> list[str]:
    rid_key = {rid: k for k, ids in zip(rec.key, rec.row_ids) for rid in ids}
    kk = {}
    for _, r in lab[(lab.alias_kind != "") & lab.row_id.isin(rid_key)].iterrows():
        kk[rid_key[r.row_id]] = r.alias_kind
    for _, r in ent[ent.has_history_alias].iterrows():
        kk.setdefault(record_key(r.history_alias_name, r.history_alias_email), str(r.history_alias_kind))
    return [kk.get(k, "") for k in rec.key]


def _in_group(kind: str, kinds, name) -> bool:
    if not kind:
        return False
    if name == "name_and_email_both":
        return "+" in kind
    return "+" not in kind and kind in kinds


def run(data_dir, out_path, seed: int = SEED) -> dict:
    led, lab, ent, rec = load(Path(data_dir), seed)
    rec["alias_kind"] = _record_kinds(lab, ent, rec)
    sub = {s: prep(rec, s) for s in ("train", "calib", "test")}
    Xtr, ytr, ptr = sub["train"][3], sub["train"][4], sub["train"][2]
    Xca, yca, pca = sub["calib"][3], sub["calib"][4], sub["calib"][2]
    rsub_te, _, pte, Xte, yte, _ = sub["test"]
    kinds_of = {s: sub[s][0].alias_kind.values for s in sub}
    out = dict(description="Leave-one-alias-kind-out recall on TEST pairs. 'seen' = trained with all kinds. "
                           "'heldout' = that kind removed from train+calibration. 'heldout_no_dedicated_features' also drops "
                           "the features written for that kind.", seed=seed, groups={})

    def touches(pairs, ks, name, kinds):
        a, b = ks[pairs[:, 0]], ks[pairs[:, 1]]
        return np.array([_in_group(x, kinds, name) or _in_group(y, kinds, name) for x, y in zip(a, b)], dtype=bool)

    def fit_eval(drop_mask_tr, drop_mask_ca, cols, tgt_mask):
        m = PairMatcher(seed).fit(Xtr[~drop_mask_tr][:, cols], ytr[~drop_mask_tr])
        m.calibrate(Xca[~drop_mask_ca][:, cols], yca[~drop_mask_ca])
        oof = getattr(m, "oof_calibrated", m.predict_proba(Xca[~drop_mask_ca][:, cols]))
        pts = curve_from_scores(oof, yca[~drop_mask_ca], int(yca[~drop_mask_ca].sum()))["points"]
        thr = float(max(pts, key=lambda d: d["f1"])["threshold"])
        p = m.predict_proba(Xte[:, cols])
        hit = (p >= thr)
        n = int(tgt_mask.sum())
        k = int((hit & tgt_mask & (yte == 1)).sum())
        other = (yte == 1) & ~tgt_mask
        return dict(threshold=thr, n_pairs=n, caught=k, recall=r4(k / n) if n else None, recall_ci95=wilson(k, n),
                    recall_other_kinds=r4((hit & other).sum() / max(other.sum(), 1)),
                    precision_overall=r4((hit & (yte == 1)).sum() / max(hit.sum(), 1)))

    all_cols = list(range(len(FEATURE_NAMES)))
    for name, (kinds, dedicated) in GROUPS.items():
        tr_mask = touches(ptr, kinds_of["train"], name, kinds)
        ca_mask = touches(pca, kinds_of["calib"], name, kinds)
        te_mask = touches(pte, kinds_of["test"], name, kinds) & (yte == 1)
        if te_mask.sum() == 0:
            continue
        keep = [i for i, f in enumerate(FEATURE_NAMES) if f not in dedicated]
        g = dict(train_pairs_removed=int(tr_mask.sum()), calib_pairs_removed=int(ca_mask.sum()), dedicated_features=dedicated,
                 seen=fit_eval(np.zeros_like(tr_mask), np.zeros_like(ca_mask), all_cols, te_mask),
                 heldout=fit_eval(tr_mask, ca_mask, all_cols, te_mask))
        if dedicated:
            g["heldout_no_dedicated_features"] = fit_eval(tr_mask, ca_mask, keep, te_mask)
        out["groups"][name] = g
    Path(out_path).write_text(json.dumps(out, indent=1))
    return out
