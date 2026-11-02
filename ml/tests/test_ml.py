import json
import numpy as np
import pandas as pd
import pytest

from payoutguard_ml import evaluate
from payoutguard_ml.anomaly import AmountAnomalyModel
from payoutguard_ml.blocking import candidate_pairs
from payoutguard_ml.explain import finalize, render_text, validate_llm_text, explain_pair
from payoutguard_ml.features import FEATURE_NAMES, feature_matrix
from payoutguard_ml.generator import ERROR_TYPES, write_dataset
from payoutguard_ml.normalize import canon_local, norm_name, soundex
from payoutguard_ml.pipeline import Analyzer
from payoutguard_ml.records import RecordTable, build_records, split_of


@pytest.fixture(scope="module")
def ds(tmp_path_factory):
    d = tmp_path_factory.mktemp("ds")
    man = write_dataset(d, seed=7, n_entities=700)
    return d, man


def test_generator_is_deterministic(tmp_path):
    a = write_dataset(tmp_path / "a", seed=11, n_entities=300)
    b = write_dataset(tmp_path / "b", seed=11, n_entities=300)
    c = write_dataset(tmp_path / "c", seed=12, n_entities=300)
    assert a["sha256"] == b["sha256"] != c["sha256"]


def test_labels_are_in_separate_files(ds):
    d, _ = ds
    led = pd.read_csv(d / "ledger.csv")
    assert not {"entity_id", "error_types", "partner_row_id", "alias_kind"} & set(led.columns)
    for f in ("current_batch.csv", "history.csv"):
        assert not {"entity_id", "error_types"} & set(pd.read_csv(d / f).columns)
    lab = pd.read_csv(d / "labels" / "row_labels.csv").fillna("")
    assert set(lab.row_id) == set(led.row_id)


def test_every_documented_error_type_is_injected(ds):
    _, man = ds
    assert set(man["injected_counts_current_batch"]) == set(ERROR_TYPES)
    assert all(v > 0 for v in man["injected_counts_current_batch"].values())


def test_outliers_only_for_payees_with_history(ds):
    d, _ = ds
    led = pd.read_csv(d / "ledger.csv")
    lab = pd.read_csv(d / "labels" / "row_labels.csv").fillna("")
    hist = lab[lab.row_id.isin(led[led.batch_id != "B2026-10"].row_id)].entity_id.value_counts()
    for e in lab[lab.error_types == "amount_outlier"].entity_id:
        assert hist.get(e, 0) >= 3


def test_split_is_by_cluster_and_deterministic():
    assert split_of("C00001", 1) == split_of("C00001", 1)
    assert {split_of(f"C{i:05d}", 3) for i in range(500)} == {"train", "calib", "test"}


def test_no_cluster_spans_splits(ds):
    d, _ = ds
    led, lab, ent, rec = evaluate.load(d, 7)
    assert rec.groupby("cluster_id").split.nunique().max() == 1


def test_normalisation_and_soundex():
    assert norm_name("Smith, Dr. John Jr.") == "john smith"
    assert canon_local("j.smith+x", "gmail.com") == "jsmith"
    assert soundex("Robert") == soundex("Rupert") == "R163"


def test_features_symmetric_and_named(ds):
    d, _ = ds
    led, lab, ent, rec = evaluate.load(d, 7)
    t = RecordTable(rec)
    pairs, st = candidate_pairs(t)
    X1 = feature_matrix(t, pairs[:300])
    X2 = feature_matrix(t, pairs[:300, ::-1])
    assert X1.shape[1] == len(FEATURE_NAMES)
    assert np.allclose(X1, X2, atol=1e-6)


def test_blocking_keeps_most_true_pairs(ds):
    d, _ = ds
    led, lab, ent, rec = evaluate.load(d, 7)
    t = RecordTable(rec)
    pairs, st = candidate_pairs(t)
    e = rec.entity_id.values
    got = int((e[pairs[:, 0]] == e[pairs[:, 1]]).sum())
    assert got / evaluate.n_true_pairs(e) > 0.9 and st["reduction_ratio"] > 0.95


def test_anomaly_model_flags_decimal_shift_and_not_normal():
    m = AmountAnomalyModel().fit({"a": [100, 102, 98, 101]}, {"a": "USD"})
    hist = [100, 102, 98, 101, 99]
    assert m.score(1000, hist)["score"] > 8 and m.score(1000, hist)["decimal_shift"] == 10.0
    assert m.score(101, hist)["score"] < 2
    cold = m.score(150, [], "USD")
    assert cold["basis"] == "cohort_prior" and cold["score"] < 3


def test_explanation_only_uses_evidence_and_validator_rejects_inventions():
    a = dict(name="Priya Sharma", email="priya@gmail.com", n_hist_rows=5, median_amount=120.0, currency="USD", last_hist_batch="B2026-09", country="IN")
    b = dict(name="P. Sharma", email="p.sharma@gmail.com", n_hist_rows=0, median_amount=118.0, currency="USD", last_hist_batch="", country="IN")
    exp = finalize(explain_pair(0.93, a, b, ["NAME_INITIAL", "ONE_RECORD_NEW"]))
    txt = exp["text"]
    assert "93%" in txt and "priya@gmail.com" in txt and "5 time(s)" in txt
    ok, _ = validate_llm_text("Very likely 'P. Sharma' <p.sharma@gmail.com> is 'Priya Sharma' (93%).", txt)
    assert ok
    ok, why = validate_llm_text("This is fraud: 'P. Sharma' was paid 700 times.", txt)
    assert not ok and any("banned" in w for w in why) and any("700" in w for w in why)
    # a bad LLM output falls back to the template text
    out = finalize(explain_pair(0.93, a, b, ["NAME_INITIAL"]), llm_fn=lambda p: "Definitely fraud, 999%")
    assert out["text_source"] == "template" and "llm_rejected_reasons" in out
    good = finalize(explain_pair(0.93, a, b, ["NAME_INITIAL"]), llm_fn=lambda p: render_text(explain_pair(0.93, a, b, ["NAME_INITIAL"])))
    assert good["text_source"] == "llm_validated"


def test_end_to_end_small(ds, tmp_path):
    d, _ = ds
    s = evaluate.run(d, tmp_path / "art", seed=7)
    for f in ("pr_curve.json", "calibration_bins.json", "review_queue.json", "anomaly_curve.json", "summary.json"):
        assert (tmp_path / "art" / "eval" / f).exists()
    pr = json.loads((tmp_path / "art" / "eval" / "pr_curve.json").read_text())
    assert len(pr["matcher_calibrated"]["points"]) == 101
    rq = json.loads((tmp_path / "art" / "eval" / "review_queue.json").read_text())
    sizes = [q["flagged_pairs"] for q in rq["identity_matcher"]]
    assert sizes == sorted(sizes, reverse=True)      # higher threshold -> never more flags
    cb = json.loads((tmp_path / "art" / "eval" / "calibration_bins.json").read_text())
    assert 0 <= cb["calibrated"]["ece"] <= 1
    an = Analyzer.load(tmp_path / "art")
    cur, hist = pd.read_csv(d / "current_batch.csv"), pd.read_csv(d / "history.csv")
    out = an.analyze(cur, hist)
    assert out["n_rows"] == len(cur) and out["findings"]
    assert all("text" in f["explanation"] for f in out["findings"])
    json.dumps(out)                                   # serialisable


def test_analyzer_rejects_bad_input(ds, tmp_path):
    d, _ = ds
    evaluate.run(d, tmp_path / "art", seed=7)
    an = Analyzer.load(tmp_path / "art")
    cur = pd.read_csv(d / "current_batch.csv")
    with pytest.raises(ValueError):
        an.analyze(cur.drop(columns=["payee_email"]))
    bad = cur.copy()
    bad.loc[bad.index[0], "amount"] = -5
    bad.loc[bad.index[1], "payee_email"] = "not-an-email"
    out = an.analyze(bad, pd.read_csv(d / "history.csv"))
    assert sum(1 for f in out["findings"] if f["type"] == "invalid_input") == 2


CONTRACT_COLS = ["row_id", "sender_item_id", "receiver_email", "recipient_name", "amount", "currency", "note", "date"]


def _contract_frames(d):
    """Exactly the agreed contract columns: no country, no batch_id."""
    ren = {"payee_email": "receiver_email", "payee_name": "recipient_name", "batch_date": "date"}
    def one(f):
        df = pd.read_csv(d / f).rename(columns=ren)
        df["sender_item_id"] = df["row_id"]
        return df[CONTRACT_COLS]
    return one("current_batch.csv"), one("history.csv")


def test_score_batch_contract(ds, tmp_path, monkeypatch):
    from payoutguard_ml import score as sc
    d, _ = ds
    evaluate.run(d, tmp_path / "art", seed=7)
    monkeypatch.setenv("PAYOUTGUARD_ARTIFACTS", str(tmp_path / "art"))
    sc._analyzer.cache_clear()
    cur, hist = _contract_frames(d)
    assert list(cur.columns) == CONTRACT_COLS
    for mode in ("model", "baseline"):
        res = sc.score_batch(cur, hist, mode=mode)
        assert [r["row_id"] for r in res] == list(cur.row_id)            # one entry per row, batch order
        assert {r["action"] for r in res} <= set(sc.ACTIONS)
        n_identity = 0
        for r in res:
            assert (r["action"] == "proceed") == (not r["flags"])
            for f in r["flags"]:
                assert f["type"] in sc.FLAG_TYPES and "linked_rows" in f and "evidence" in f
                if f["type"] in ("duplicate_payee", "typo_receiver") and not f["evidence"].get("exact_row_repeat"):
                    assert f["evidence"]["category"] in ("new_record_matches_known_payee", "two_records_same_payee")
                    n_identity += 1
        assert n_identity > 0
        json.dumps(res)
    ids = [r["row_id"] for r in sc.score_batch(cur, hist)]
    assert ids == list(cur.row_id)


def test_score_batch_contract_columns_catch_injected_identity_errors(ds, tmp_path, monkeypatch):
    """Regression: without `country` in the input, alias duplicates and email typos must still be flagged."""
    from payoutguard_ml import score as sc
    d, _ = ds
    evaluate.run(d, tmp_path / "art", seed=7)
    monkeypatch.setenv("PAYOUTGUARD_ARTIFACTS", str(tmp_path / "art"))
    sc._analyzer.cache_clear()
    cur, hist = _contract_frames(d)
    act = {r["row_id"]: r["action"] for r in sc.score_batch(cur, hist)}
    lab = pd.read_csv(d / "labels" / "row_labels.csv").fillna("")
    ent = pd.read_csv(d / "labels" / "entity_labels.csv")
    cluster = dict(zip(ent.entity_id, ent.cluster_id))
    inj = lab[lab.row_id.isin(act) & lab.error_types.isin(["alias_duplicate", "email_typo"])]
    test_ids = [r for r, e in zip(inj.row_id, inj.entity_id) if split_of(cluster[e], 7) == "test"]
    assert len(test_ids) >= 5
    rate = sum(act[r] != "proceed" for r in test_ids) / len(test_ids)
    print(f"contract-path identity recall on test split: {rate:.3f} ({len(test_ids)} rows)")
    assert rate >= 0.8


def test_score_batch_ignores_country(ds, tmp_path, monkeypatch):
    """The contract has no country column, so adding one must not change anything."""
    from payoutguard_ml import score as sc
    d, _ = ds
    evaluate.run(d, tmp_path / "art", seed=7)
    monkeypatch.setenv("PAYOUTGUARD_ARTIFACTS", str(tmp_path / "art"))
    sc._analyzer.cache_clear()
    cur, hist = _contract_frames(d)
    country = dict(zip(*pd.read_csv(d / "ledger.csv")[["row_id", "country"]].values.T))
    without = sc.score_batch(cur, hist)
    with_country = sc.score_batch(cur.assign(country=cur.row_id.map(country)), hist.assign(country=hist.row_id.map(country)))
    assert json.dumps(without, sort_keys=True) == json.dumps(with_country, sort_keys=True)


def test_score_batch_history_id_clash_is_prefixed(ds, tmp_path, monkeypatch):
    from payoutguard_ml import score as sc
    d, _ = ds
    evaluate.run(d, tmp_path / "art", seed=7)
    monkeypatch.setenv("PAYOUTGUARD_ARTIFACTS", str(tmp_path / "art"))
    sc._analyzer.cache_clear()
    cur, hist = _contract_frames(d)
    cur = cur.copy(); hist = hist.copy()
    hist["row_id"] = hist["row_id"].str.replace("R", "r")
    cur["row_id"] = cur["row_id"].str.replace("R", "r")
    cur.loc[cur.index[:50], "row_id"] = hist.row_id.iloc[:50].values      # force clashes
    res = sc.score_batch(cur, hist)
    linked = [x for r in res for f in r["flags"] for x in f["linked_rows"]]
    assert all(x.startswith("history:") or x in set(cur.row_id) for x in linked)
