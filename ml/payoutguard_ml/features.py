"""Symmetric pair features. Name-, email- and context-based; no row-count or batch-overlap features."""
from __future__ import annotations
import math
import numpy as np
from rapidfuzz import fuzz
from rapidfuzz.distance import JaroWinkler, Levenshtein

from .names import FREE_DOMAINS
from .records import RecordTable

FEATURE_NAMES = [
    "nm_ratio", "nm_jw", "nm_token_sort", "nm_token_set", "nm_partial", "tok_jaccard", "tok_shared",
    "initial_compat", "aligned_jw", "n_tok_min", "n_tok_absdiff", "name_equal", "nick_used",
    "em_local_jw", "em_local_ratio", "em_canon_equal", "em_domain_equal", "em_both_free",
    "em_lev", "em_lev_le1", "em_alpha_jw", "em_alpha_equal",
    "ne_cons_min", "ne_cons_max", "ne_cross",
    "currency_equal", "amt_logratio",
]
_FREE = set(FREE_DOMAINS)


def _aligned(ta: list[str], tb: list[str]) -> float:
    if not ta or not tb:
        return 0.0
    def sc(x, y):
        return (JaroWinkler.similarity(x[0], y[0]) + JaroWinkler.similarity(x[-1], y[-1])) / 2
    return max(sc(ta, tb), sc(ta, tb[::-1]))


def _initial_compat(ta: list[str], tb: list[str]) -> float:
    for s, o in ((ta, tb), (tb, ta)):
        for t in s:
            if len(t) == 1 and any(len(u) > 1 and u[0] == t for u in o):
                return 1.0
    return 0.0


def pair_features(t: RecordTable, i: int, j: int) -> list[float]:
    na, nb = t.name_norm[i], t.name_norm[j]
    ta, tb = t.tokens_exp[i], t.tokens_exp[j]
    ra, rb = t.tokens[i], t.tokens[j]
    sa, sb = set(ta), set(tb)
    shared = len(sa & sb)
    la, lb = t.local[i], t.local[j]
    aa, ab = t.local_alpha[i], t.local_alpha[j]
    ea, eb = t.email[i].lower(), t.email[j].lower()
    ja, jb = "".join(ra), "".join(rb)
    cons_a = fuzz.partial_ratio(aa, ja) / 100 if aa and ja else 0.0
    cons_b = fuzz.partial_ratio(ab, jb) / 100 if ab and jb else 0.0
    cross = max(fuzz.partial_ratio(aa, jb), fuzz.partial_ratio(ab, ja)) / 100 if (aa and jb and ab and ja) else 0.0
    lev = Levenshtein.distance(ea, eb)
    return [
        fuzz.ratio(na, nb) / 100, JaroWinkler.similarity(na, nb), fuzz.token_sort_ratio(na, nb) / 100,
        fuzz.token_set_ratio(na, nb) / 100, fuzz.partial_ratio(na, nb) / 100,
        shared / max(len(sa | sb), 1), float(shared), _initial_compat(ra, rb), _aligned(ta, tb),
        float(min(len(ra), len(rb))), float(abs(len(ra) - len(rb))), float(na == nb),
        float(any(x != y and ex == ey for x, ex in zip(ra, ta) for y, ey in zip(rb, tb))),
        JaroWinkler.similarity(la, lb), fuzz.ratio(la, lb) / 100, float(la == lb and bool(la)),
        float(t.domain[i] == t.domain[j]), float(t.domain[i] in _FREE and t.domain[j] in _FREE),
        float(lev), float(lev <= 1), JaroWinkler.similarity(aa, ab), float(aa == ab and bool(aa)),
        min(cons_a, cons_b), max(cons_a, cons_b), cross,
        1.0 if t.currency[i] == t.currency[j] else 0.0,
        abs(t.log_median[i] - t.log_median[j]),
    ]


def feature_matrix(t: RecordTable, pairs: np.ndarray) -> np.ndarray:
    if len(pairs) == 0:
        return np.zeros((0, len(FEATURE_NAMES)), dtype=np.float32)
    return np.asarray([pair_features(t, int(i), int(j)) for i, j in pairs], dtype=np.float32)
