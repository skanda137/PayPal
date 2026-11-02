"""Per-payee amount anomaly model: robust log-space z-score with empirical-Bayes shrinkage.

For a payee with n prior payouts: centre = median(log amount), spread = MAD-based sigma shrunk toward
the pooled within-payee sigma (k0 pseudo-observations). Payees with fewer than `min_hist` payouts fall
back to a currency cohort (wide spread), so only extreme amounts are flagged for new payees.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np

MAD_TO_SIGMA = 1.4826


@dataclass
class AmountAnomalyModel:
    k0: float = 6.0
    min_hist: int = 3
    min_sigma: float = 0.10
    sigma0: float = 0.2
    cohort: dict = field(default_factory=dict)       # currency -> (median_log, sigma)
    global_cohort: tuple = (np.log(150.0), 1.2)
    threshold: float = 4.0
    predictive: bool = True      # inflate sigma by sqrt(1+1/n): a new draw is noisier than the median estimate

    def fit(self, hist: dict[str, list[float]], currency_of: dict[str, str]) -> "AmountAnomalyModel":
        """hist: payee key -> past amounts. Learns pooled within-payee sigma and currency cohorts."""
        devs, by_cur = [], {}
        for k, a in hist.items():
            la = np.log(np.maximum(np.asarray(a, float), 0.01))
            by_cur.setdefault(currency_of.get(k, "USD"), []).append(np.median(la))
            if len(la) >= self.min_hist:
                devs.append(MAD_TO_SIGMA * np.median(np.abs(la - np.median(la))))
        if devs:
            self.sigma0 = float(max(np.median([d for d in devs if d > 0] or [0.2]), self.min_sigma))
        allm = np.concatenate([v for v in by_cur.values()]) if by_cur else np.array([np.log(150.0)])
        self.global_cohort = (float(np.median(allm)), float(max(MAD_TO_SIGMA * np.median(np.abs(allm - np.median(allm))), 0.5)))
        self.cohort = {c: (float(np.median(v)), float(max(MAD_TO_SIGMA * np.median(np.abs(np.asarray(v) - np.median(v))), 0.5)))
                       for c, v in by_cur.items() if len(v) >= 30}
        return self

    def score(self, amount: float, history: list[float] | None, currency: str = "USD") -> dict:
        """Return evidence dict: z (signed), score=|z|, expected range, ratio, decimal-shift hint."""
        x = float(max(amount, 0.01))
        lx = np.log(x)
        h = np.asarray(history if history is not None else [], float)
        n = len(h)
        if n >= self.min_hist:
            lh = np.log(np.maximum(h, 0.01))
            centre = float(np.median(lh))
            s_hat = MAD_TO_SIGMA * float(np.median(np.abs(lh - centre)))
            sigma = float(np.sqrt((n * s_hat ** 2 + self.k0 * self.sigma0 ** 2) / (n + self.k0)))
            sigma = max(sigma, self.min_sigma)
            if self.predictive:
                sigma *= float(np.sqrt(1 + 1 / n))
            basis = "payee_history"
        else:
            centre, sigma = self.cohort.get(currency, self.global_cohort)
            basis = "cohort_prior"
        z = float((lx - centre) / sigma)
        med = float(np.exp(centre))
        ratio = x / med
        shift = None
        for f in (10.0, 100.0, 0.1, 0.01):
            if abs(ratio / f - 1) < 0.15:
                shift = f
        lo, hi = float(np.exp(centre - 2 * sigma)), float(np.exp(centre + 2 * sigma))
        return dict(z=round(z, 3), score=round(abs(z), 3), amount=round(x, 2), payee_median=round(med, 2), ratio=round(ratio, 3),
                    n_history=int(n), basis=basis, typical_low=round(lo, 2), typical_high=round(hi, 2),
                    decimal_shift=shift, sigma=round(sigma, 4))
