"""rapidfuzz baselines. 'name': token_sort_ratio on normalised names. 'combined': mean of that and
the email-local ratio. Scored brute-force over ALL pairs (no blocking), which favours the baseline."""
from __future__ import annotations
import numpy as np
from rapidfuzz import fuzz, process

from .records import RecordTable

KINDS = ("name", "combined")


def all_pairs_matrix(t: RecordTable, kind: str) -> np.ndarray:
    nm = process.cdist(t.name_norm, t.name_norm, scorer=fuzz.token_sort_ratio, dtype=np.uint8, workers=-1).astype(np.float32)
    if kind == "name":
        return nm / 100.0
    em = process.cdist(t.local, t.local, scorer=fuzz.ratio, dtype=np.uint8, workers=-1).astype(np.float32)
    return (nm + em) / 200.0


def score_pairs(t: RecordTable, pairs: np.ndarray, kind: str) -> np.ndarray:
    out = np.empty(len(pairs), dtype=np.float32)
    for k, (i, j) in enumerate(pairs):
        s = fuzz.token_sort_ratio(t.name_norm[i], t.name_norm[j])
        if kind == "combined":
            s = (s + fuzz.ratio(t.local[i], t.local[j])) / 2
        out[k] = s / 100.0
    return out
