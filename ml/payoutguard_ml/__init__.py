"""PayoutGuard ML: synthetic ledger, pair matcher, amount anomaly model, evaluation."""
SEED = 2026  # default seed for every stage; all randomness derives from it
__version__ = "0.1.0"
from .score import score_batch  # noqa: E402,F401  (team contract entry point)
