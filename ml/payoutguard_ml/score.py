"""Team contract: score_batch(batch_df, history_df) -> list[dict], one dict per batch row.

Input columns (both CSVs): row_id, sender_item_id, receiver_email, recipient_name, amount, currency, note, date
(sender_item_id, note, date optional). Output per row:
  {"row_id": "r17", "action": "proceed|review|hold",
   "flags": [{"type": "duplicate_payee|typo_receiver|amount_anomaly|invalid_input", "prob": 0.91 | None,
              "linked_rows": ["r04"], "evidence": {...}, "explanation": "..."}]}
Linked history rows that would clash with a batch row_id are returned as "history:<row_id>".
Advisory only: the caller decides whether anything is sent.
"""
from __future__ import annotations
import os
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from .features import FEATURE_NAMES
from .pipeline import Analyzer

FLAG_TYPES = ("duplicate_payee", "typo_receiver", "amount_anomaly", "invalid_input")  # invalid_input is an extension
ACTIONS = ("proceed", "review", "hold")
HOLD_PROB = 0.90
BASELINE_THRESHOLD = 0.91   # rapidfuzz name+email score giving a queue about the size of the model's (see eval)
_NAME_COLS = ["row_id", "receiver_email", "recipient_name", "amount", "currency"]


class BaselineMatcher:
    """Same interface as PairMatcher but scores with the rapidfuzz baseline: mean of token_sort_ratio (names)
    and ratio (email local parts). Uncalibrated: the number is a similarity, not a probability."""
    threshold = BASELINE_THRESHOLD

    def predict_proba(self, X):
        return (X[:, FEATURE_NAMES.index("nm_token_sort")] + X[:, FEATURE_NAMES.index("em_local_ratio")]) / 2


def _artifacts() -> Path:
    return Path(os.environ.get("PAYOUTGUARD_ARTIFACTS", Path(__file__).resolve().parent.parent / "artifacts"))


@lru_cache(maxsize=2)
def _analyzer(mode: str) -> Analyzer:
    an = Analyzer.load(_artifacts())
    if mode == "baseline":
        an.matcher = BaselineMatcher()
    elif mode != "model":
        raise ValueError("mode must be 'model' or 'baseline'")
    return an


def _internal(batch: pd.DataFrame, history: pd.DataFrame | None):
    miss = [c for c in _NAME_COLS if c not in batch.columns]
    if miss:
        raise ValueError(f"batch_df missing columns {miss}")
    ren = {"receiver_email": "payee_email", "recipient_name": "payee_name"}
    b = batch.rename(columns=ren).copy()
    b["row_id"] = b["row_id"].astype(str)
    b["batch_id"] = "CURRENT"
    clash = False
    h = None
    if history is not None and len(history):
        miss = [c for c in _NAME_COLS if c not in history.columns]
        if miss:
            raise ValueError(f"history_df missing columns {miss}")
        h = history.rename(columns=ren).copy()
        h["row_id"] = h["row_id"].astype(str)
        h["batch_id"] = ("H" + h["date"].astype(str).str[:7]) if "date" in h.columns else "HIST"
        clash = bool(set(b.row_id) & set(h.row_id))
        if clash:
            h["row_id"] = "history:" + h["row_id"]
    if b.row_id.duplicated().any():
        raise ValueError("batch_df row_id must be unique")
    return b, h


def _flag_from_identity(f: dict, row_id: str) -> dict:
    recs = f["records"]
    own = 0 if row_id in recs[0]["row_ids"] else 1
    other = recs[1 - own]
    sig, feat = set(f["signals"]), f["features"]
    typo = "EMAIL_ONE_EDIT" in sig and "NAME_IDENTICAL" in sig and "ONE_RECORD_NEW" in sig
    return dict(type="typo_receiver" if typo else "duplicate_payee", prob=f["probability"], linked_rows=list(other["row_ids"]),
                evidence=dict(name_sim=round(feat["nm_token_sort"], 3), email_edit_dist=int(feat["em_lev"]),
                              signals=f["signals"], category=f["category"]),
                explanation=f["explanation"]["text"])


def score_batch(batch_df: pd.DataFrame, history_df: pd.DataFrame | None = None, mode: str = "model",
                er_threshold: float | None = None, llm_fn=None) -> list[dict]:
    b, h = _internal(batch_df, history_df)
    out = _analyzer(mode).analyze(b, h, er_threshold=er_threshold, llm_fn=llm_fn)
    per_row = {rid: [] for rid in b.row_id}
    hold = set()
    for f in out["findings"]:
        t = f["type"]
        if t == "identity_match":
            if not f["above_threshold"]:
                continue
            for rid in f["affected_row_ids"]:
                per_row[rid].append(_flag_from_identity(f, rid))
                recs = f["records"]
                own = recs[0] if rid in recs[0]["row_ids"] else recs[1]
                # hold only the never-paid record of a very likely match; established payees with two records get review
                if f["probability"] >= HOLD_PROB and mode == "model" and own["n_hist_rows"] == 0 and f["category"] == "new_record_matches_known_payee":
                    hold.add(rid)
        elif t == "exact_duplicate":
            for rid in f["affected_row_ids"]:
                per_row[rid].append(dict(type="duplicate_payee", prob=1.0, linked_rows=[r for r in f["affected_row_ids"] if r != rid],
                                         evidence=dict(name_sim=1.0, email_edit_dist=0, exact_row_repeat=True),
                                         explanation=f["explanation"]["text"]))
                hold.add(rid)
        elif t == "amount_anomaly":
            ev = f["evidence"]
            rid = f["affected_row_ids"][0]
            per_row[rid].append(dict(type="amount_anomaly", prob=None, linked_rows=[],
                                     evidence=dict(z=ev["z"], ratio_to_median=ev["ratio"], payee_median=ev["payee_median"],
                                                   n_history=ev["n_history"], decimal_shift=ev["decimal_shift"], history_source=ev["history_source"]),
                                     explanation=f["explanation"]["text"]))
            if ev["decimal_shift"] or ev["ratio"] >= 10 or ev["ratio"] <= 0.1:
                hold.add(rid)
        elif t == "invalid_input":
            rid = f["affected_row_ids"][0]
            per_row[rid].append(dict(type="invalid_input", prob=None, linked_rows=[], evidence={}, explanation=f["explanation"]["text"]))
            hold.add(rid)
    res = []
    for rid in b.row_id:       # batch order preserved
        flags = per_row[rid]
        res.append(dict(row_id=rid, action="hold" if rid in hold else "review" if flags else "proceed", flags=flags))
    return res
