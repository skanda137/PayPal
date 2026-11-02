"""Inference entry point used by the backend. Advisory only: it never moves money."""
from __future__ import annotations
import re
from pathlib import Path
import joblib
import numpy as np
import pandas as pd

from .anomaly import AmountAnomalyModel
from .blocking import candidate_pairs
from .explain import explain_amount, explain_pair, finalize, pair_signals
from .features import FEATURE_NAMES, feature_matrix
from .matcher import PairMatcher
from .normalize import record_key
from .records import RecordTable, build_records

REQUIRED = ["row_id", "batch_id", "payee_name", "payee_email", "amount", "currency"]
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DISCLAIMER = ("Advisory output. Payment execution stays deterministic and requires explicit operator approval. "
              "Probabilities are calibrated on synthetic data.")


def _py(o):
    if isinstance(o, dict):
        return {k: _py(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_py(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


class Analyzer:
    def __init__(self, matcher: PairMatcher, anomaly: AmountAnomalyModel):
        self.matcher, self.anomaly = matcher, anomaly

    @classmethod
    def load(cls, artifacts_dir: str | Path) -> "Analyzer":
        d = Path(artifacts_dir) / "models"
        return cls(PairMatcher.load(d / "matcher.joblib"), joblib.load(d / "anomaly.joblib"))

    @staticmethod
    def _prep(df: pd.DataFrame, name: str) -> pd.DataFrame:
        miss = [c for c in REQUIRED if c not in df.columns]
        if miss:
            raise ValueError(f"{name}: missing columns {miss}")
        d = df.copy()
        if "country" not in d.columns:
            d["country"] = ""
        d["country"] = d["country"].fillna("")
        d["amount"] = pd.to_numeric(d["amount"], errors="coerce")
        return d

    def analyze(self, batch: pd.DataFrame, history: pd.DataFrame | None = None, er_threshold: float | None = None,
                z_threshold: float | None = None, min_prob: float = 0.2, llm_fn=None) -> dict:
        tau = self.matcher.threshold if er_threshold is None else er_threshold
        zthr = self.anomaly.threshold if z_threshold is None else z_threshold
        batch = self._prep(batch, "batch")
        hist = self._prep(history, "history") if history is not None and len(history) else batch.iloc[0:0]
        cur_ids = batch.batch_id.unique()
        if len(cur_ids) != 1:
            raise ValueError("batch must contain exactly one batch_id")
        cur_id = cur_ids[0]
        findings: list[dict] = []

        # deterministic input validation
        bad = batch[batch.amount.isna() | (batch.amount <= 0) | ~batch.payee_email.astype(str).str.match(_EMAIL_RE)]
        for _, r in bad.iterrows():
            why = "amount is missing, zero or negative" if (pd.isna(r.amount) or r.amount <= 0) else "email address is not valid"
            findings.append(dict(type="invalid_input", severity="high", affected_row_ids=[r.row_id], probability=None,
                                 explanation=finalize(dict(headline=f"Row {r.row_id} cannot be paid as written: {why}.", bullets=[], caution="",
                                                           severity="high", facts=dict(row_id=r.row_id)))))
        batch_ok = batch[~batch.row_id.isin(bad.row_id)]
        full = pd.concat([hist, batch_ok], ignore_index=True)
        rec = build_records(full, cur_id)
        tbl = RecordTable(rec)
        pairs, bstats = candidate_pairs(tbl)
        in_cur = rec.in_current.values
        if len(pairs):
            pairs = pairs[in_cur[pairs[:, 0]] | in_cur[pairs[:, 1]]]
        X = feature_matrix(tbl, pairs)
        prob = self.matcher.predict_proba(X) if len(pairs) else np.zeros(0)

        flagged_partner: dict[int, list[tuple[int, float]]] = {}
        n = 0
        for (i, j), p, x in sorted(zip(map(tuple, pairs), prob, X), key=lambda t: -t[1]):
            if p < min_prob:
                continue
            a, b = (rec.iloc[i], rec.iloc[j])
            ai = dict(name=a["name"], email=a["email"], country=a["country"], n_hist_rows=int(a.n_hist_rows), currency=a.currency,
                      median_amount=float(a.median_amount), last_hist_batch=a.last_hist_batch, row_ids=list(a.row_ids))
            bi = dict(name=b["name"], email=b["email"], country=b["country"], n_hist_rows=int(b.n_hist_rows), currency=b.currency,
                      median_amount=float(b.median_amount), last_hist_batch=b.last_hist_batch, row_ids=list(b.row_ids))
            f = dict(zip(FEATURE_NAMES, map(float, x)))
            sig = pair_signals(f, ai, bi)
            n += 1
            rows = sorted(set((a.cur_row_ids if in_cur[i] else []) + (b.cur_row_ids if in_cur[j] else [])))
            if p >= tau:
                flagged_partner.setdefault(i, []).append((j, p))
                flagged_partner.setdefault(j, []).append((i, p))
            findings.append(dict(
                finding_id=f"ID-{n:04d}", type="identity_match", probability=round(float(p), 4), severity="high" if p >= 0.9 else "medium" if p >= 0.7 else "low",
                above_threshold=bool(p >= tau),
                category="new_record_matches_known_payee" if (a.n_hist_rows == 0) != (b.n_hist_rows == 0) else "two_records_same_payee",
                affected_row_ids=rows, records=[ai, bi], signals=sig, features=f,
                explanation=finalize(explain_pair(float(p), ai, bi, sig), llm_fn),
                recommended_action="hold_for_review" if p >= tau else "note"))

        # exact duplicates (deterministic rule)
        cur = batch_ok.copy()
        cur["key"] = [record_key(a, b) for a, b in zip(cur.payee_name, cur.payee_email)]
        for (_, amt), g in cur.groupby(["key", "amount"]):
            if len(g) > 1:
                r0 = g.iloc[0]
                exp = dict(headline=f"'{r0.payee_name}' <{r0.payee_email}> appears {len(g)} times in this batch for the same amount of {amt:,.2f} {r0.currency}.",
                           bullets=["Identical payee and amount repeated within one batch."], caution="", severity="high",
                           facts=dict(count=len(g), amount=float(amt)))
                findings.append(dict(type="exact_duplicate", severity="high", probability=None, affected_row_ids=list(g.row_id),
                                     explanation=finalize(exp, llm_fn), recommended_action="hold_for_review"))

        # amount anomalies: use the record's own history; if thin, borrow the linked payee's history
        hist_amts = full[full.batch_id != cur_id].assign(key=lambda d: [record_key(a, b) for a, b in zip(d.payee_name, d.payee_email)]) \
            .groupby("key").amount.agg(list).to_dict()
        key_to_idx = {k: i for i, k in enumerate(rec.key)}
        for _, r in cur.iterrows():
            own = hist_amts.get(r.key, [])
            h, basis_note = own, "own_history"
            if len(own) < self.anomaly.min_hist:
                ridx = key_to_idx[r.key]
                best = max(((hist_amts.get(rec.key.iat[j], []), j) for j, _p in flagged_partner.get(ridx, [])), key=lambda t: len(t[0]), default=([], None))
                if len(best[0]) >= self.anomaly.min_hist:
                    h, basis_note = best[0], "linked_payee_history"
            ev = self.anomaly.score(r.amount, h, r.currency)
            if ev["score"] >= zthr:
                exp = explain_amount(ev, r.payee_name, r.currency)
                if basis_note == "linked_payee_history":
                    exp["bullets"].append("The comparison uses the history of the payee this record is likely linked to.")
                findings.append(dict(type="amount_anomaly", severity=exp["severity"], probability=None, affected_row_ids=[r.row_id],
                                     evidence=dict(ev, history_source=basis_note), explanation=finalize(exp, llm_fn),
                                     recommended_action="hold_for_review"))
        flagged = set()
        for f_ in findings:
            if f_["type"] != "identity_match" or f_["above_threshold"]:
                flagged.update(f_["affected_row_ids"])
        return _py(dict(
            batch_id=cur_id, n_rows=int(len(batch)), findings=findings,
            summary=dict(n_findings=len(findings), flagged_rows=len(flagged), flagged_rows_per_1000=round(1000 * len(flagged) / max(len(batch), 1), 1),
                         by_type={t: sum(1 for f_ in findings if f_["type"] == t) for t in sorted({f_["type"] for f_ in findings})},
                         er_threshold=tau, z_threshold=zthr, blocking=bstats),
            disclaimer=DISCLAIMER))
