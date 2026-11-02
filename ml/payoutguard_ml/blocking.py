"""Candidate generation. Keys are cheap and phonetic so typos/initials/reordering/nicknames survive."""
from __future__ import annotations
from collections import defaultdict
from itertools import combinations
import numpy as np

from .normalize import soundex
from .records import RecordTable

BLOCK_CAP = 80   # blocks larger than this are skipped (and counted); common names are not enumerated


def record_keys(tbl: RecordTable, i: int) -> list[str]:
    keys: list[str] = []
    toks = tbl.tokens[i]
    forms = [sorted({t, e}) for t, e in zip(toks, tbl.tokens_exp[i])]
    if len(toks) == 1:
        keys += [f"n1:{soundex(f)}" for f in forms[0] if len(f) >= 3]
    for a, b in combinations(range(len(toks)), 2):
        for pa, pb in ((a, b), (b, a)):
            for fa in forms[pa]:
                for fb in forms[pb]:
                    if len(fb) >= 3:
                        keys.append(f"c:{fa[0]}|{soundex(fb)}")
    la = tbl.local_alpha[i]
    if len(la) >= 4:
        keys.append(f"e:{la[:6]}")
        keys.append(f"ex:{soundex(la)}@{tbl.domain[i]}")
    return keys


def candidate_pairs(tbl: RecordTable, cap: int = BLOCK_CAP) -> tuple[np.ndarray, dict]:
    blocks: dict[str, list[int]] = defaultdict(list)
    for i in range(tbl.n):
        for k in set(record_keys(tbl, i)):
            blocks[k].append(i)
    pairs: set[tuple[int, int]] = set()
    skipped = 0
    for k in sorted(blocks):
        m = blocks[k]
        if len(m) < 2:
            continue
        if len(m) > cap:
            skipped += 1
            continue
        for x, y in combinations(m, 2):
            pairs.add((x, y) if x < y else (y, x))
    arr = np.array(sorted(pairs), dtype=np.int64).reshape(-1, 2)
    total = tbl.n * (tbl.n - 1) // 2
    return arr, dict(n_records=tbl.n, n_candidates=len(arr), n_all_pairs=total,
                     reduction_ratio=1 - len(arr) / max(total, 1), oversized_blocks_skipped=skipped)
