"""Gradient-boosted pair matcher with probability calibration."""
from __future__ import annotations
import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold

from . import SEED
from .features import FEATURE_NAMES


def _logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p)).reshape(-1, 1)


class PairMatcher:
    def __init__(self, seed: int = SEED):
        self.seed = seed
        self.model = HistGradientBoostingClassifier(
            max_iter=400, learning_rate=0.06, max_leaf_nodes=15, min_samples_leaf=10, l2_regularization=1.0,
            early_stopping=True, validation_fraction=0.15, n_iter_no_change=25, random_state=seed)
        self.calibrator = None
        self.calibration_method = "none"
        self.threshold = 0.5
        self.feature_names = list(FEATURE_NAMES)

    def fit(self, X, y) -> "PairMatcher":
        self.model.fit(X, y)
        return self

    def raw(self, X) -> np.ndarray:
        return self.model.predict_proba(X)[:, 1]

    def calibrate(self, X, y) -> dict:
        """Choose isotonic vs Platt by 5-fold CV Brier score on the calibration split, then refit."""
        s, y = self.raw(X), np.asarray(y)
        fits = {"isotonic": lambda a, b: IsotonicRegression(out_of_bounds="clip").fit(a, b),
                "sigmoid": lambda a, b: LogisticRegression(C=1e6).fit(_logit(a), b)}
        pred = {"isotonic": lambda c, a: c.predict(a), "sigmoid": lambda c, a: c.predict_proba(_logit(a))[:, 1]}
        brier, oof = {}, {}
        if y.sum() >= 20:
            for m in fits:
                out = np.zeros(len(y))
                for tr, te in StratifiedKFold(5, shuffle=True, random_state=self.seed).split(s, y):
                    out[te] = pred[m](fits[m](s[tr], y[tr]), s[te])
                brier[m], oof[m] = float(np.mean((out - y) ** 2)), out
            best = min(brier, key=brier.get)
            self.oof_calibrated = oof[best]
        else:
            best = "sigmoid"
        self.calibrator = fits[best](s, y)
        self._pred = best
        self.calibration_method = best
        return dict(method=best, cv_brier=brier, n_pos=int(y.sum()), n=int(len(y)))

    def predict_proba(self, X) -> np.ndarray:
        s = self.raw(X)
        if self.calibrator is None:
            return s
        if self._pred == "isotonic":
            return self.calibrator.predict(s)
        return self.calibrator.predict_proba(_logit(s))[:, 1]

    def save(self, path):
        joblib.dump(self, path)

    @staticmethod
    def load(path) -> "PairMatcher":
        return joblib.load(path)
