"""Turn ledger rows into distinct payee records (the unit the matcher links)."""
from __future__ import annotations
import hashlib
import numpy as np
import pandas as pd

from .normalize import (canon_local, expand_token, local_alpha, name_tokens, norm_name, record_key,
                        split_email)


class RecordTable:
    """Distinct (name, email) records plus precomputed normalised fields (plain lists for speed)."""

    def __init__(self, df: pd.DataFrame):
        self.df = df.reset_index(drop=True)
        d = self.df
        self.n = len(d)
        self.name = d["name"].tolist()
        self.email = d["email"].tolist()
        self.name_norm = [norm_name(x) for x in self.name]
        self.tokens = [x.split() for x in self.name_norm]
        self.tokens_exp = [[expand_token(t) for t in ts] for ts in self.tokens]
        parts = [split_email(e) for e in self.email]
        self.domain = [p[1] for p in parts]
        self.local = [canon_local(p[0], p[1]) for p in parts]
        self.local_alpha = [local_alpha(x) for x in self.local]
        self.country = d["country"].tolist()
        self.currency = d["currency"].tolist()
        self.median_amount = d["median_amount"].astype(float).tolist()
        self.log_median = [float(np.log(max(m, 0.01))) for m in self.median_amount]


def build_records(ledger: pd.DataFrame, current_batch_id: str) -> pd.DataFrame:
    """One row per distinct payee record. `ledger` needs row_id,batch_id,payee_name,payee_email,
    country,amount,currency. Never reads labels."""
    d = ledger.copy()
    d["amount"] = d["amount"].astype(float)
    d["country"] = d["country"].fillna("").astype(str)
    d["currency"] = d["currency"].fillna("USD").astype(str)
    d["key"] = [record_key(n, e) for n, e in zip(d.payee_name, d.payee_email)]
    d["is_cur"] = d.batch_id == current_batch_id
    g = d.groupby("key", sort=True)
    hist = d[~d.is_cur]
    rec = pd.DataFrame({
        "key": list(g.groups.keys()),
        "name": g["payee_name"].first().values,
        "email": g["payee_email"].first().values,
        "country": g["country"].agg(lambda s: s.mode().iat[0]).values,
        "currency": g["currency"].agg(lambda s: s.mode().iat[0]).values,
        "n_rows": g.size().values,
        "median_amount": g["amount"].median().values,
        "in_current": g["is_cur"].any().values,
        "row_ids": g["row_id"].agg(list).values,
    })
    hg = hist.groupby("key")
    rec["n_hist_rows"] = rec.key.map(hg.size()).fillna(0).astype(int)
    rec["last_hist_batch"] = rec.key.map(hg["batch_id"].max()).fillna("")
    cur_ids = d[d.is_cur].groupby("key")["row_id"].agg(list)
    rec["cur_row_ids"] = rec.key.map(cur_ids).apply(lambda x: x if isinstance(x, list) else [])
    rec["rid"] = np.arange(len(rec))
    return rec


def split_of(cluster_id: str, seed: int) -> str:
    """Deterministic 50/20/30 train/calib/test split by entity cluster (relatives stay together)."""
    h = int(hashlib.sha256(f"{seed}:{cluster_id}".encode()).hexdigest(), 16) % 100
    return "train" if h < 50 else ("calib" if h < 70 else "test")
