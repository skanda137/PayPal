"""python -m payoutguard_ml.cli {generate,evaluate,demo,analyze,all}"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

import pandas as pd

from . import SEED
from .generator import write_dataset


def main(argv=None):
    ap = argparse.ArgumentParser(prog="payoutguard_ml")
    ap.add_argument("cmd", choices=["generate", "evaluate", "demo", "analyze", "all"])
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--n-entities", type=int, default=6000)
    ap.add_argument("--data", default="data/eval")
    ap.add_argument("--artifacts", default="artifacts")
    ap.add_argument("--hard", action="store_true", help="harder generator difficulty (separate dataset)")
    ap.add_argument("--batch"), ap.add_argument("--history")
    a = ap.parse_args(argv)
    if a.cmd in ("generate", "all"):
        m = write_dataset(a.data, a.seed, a.n_entities, hard=a.hard)
        print("generated", a.data, m["n_rows"], "rows; injected:", m["injected_counts_current_batch"])
    if a.cmd in ("evaluate", "all"):
        from . import evaluate
        s = evaluate.run(a.data, a.artifacts, a.seed)
        print(json.dumps(s["identity_matcher"]["at_threshold"], indent=1))
    if a.cmd in ("demo", "all"):
        from .pipeline import Analyzer
        demo = Path(a.artifacts) / "demo"
        write_dataset(demo / "data", a.seed + 1, 400)
        an = Analyzer.load(a.artifacts)
        out = an.analyze(pd.read_csv(demo / "data" / "current_batch.csv"), pd.read_csv(demo / "data" / "history.csv"))
        (demo / "demo_findings.json").write_text(json.dumps(out, indent=1))
        print("demo findings:", out["summary"]["by_type"])
    if a.cmd == "analyze":
        from .pipeline import Analyzer
        out = Analyzer.load(a.artifacts).analyze(pd.read_csv(a.batch), pd.read_csv(a.history) if a.history else None)
        print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
