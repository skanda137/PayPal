import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis,
} from "recharts";
import { api } from "../api.js";

const SERIES = [
  ["matcher_calibrated", "Matcher (gradient boosting)", "#2563eb"],
  ["baseline_logreg_same_features", "Logistic regression, same features", "#7c3aed"],
  ["baseline_name_plus_email", "rapidfuzz name + email", "#d97706"],
  ["baseline_name_token_sort", "rapidfuzz name only", "#9ca3af"],
];

const pct = (x) => (x == null ? "—" : `${(x * 100).toFixed(1)}%`);
const ci = (c) => (c ? `${c[0].toFixed(2)}–${c[1].toFixed(2)}` : "");

function Synthetic() {
  return <span className="synthetic">synthetic data</span>;
}

export default function Eval() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [qi, setQi] = useState(null);

  useEffect(() => {
    api.eval().then(setData, setError);
  }, []);

  const pr = useMemo(() => {
    if (!data) return [];
    return SERIES.filter(([k]) => data.pr_curve[k]).map(([k, label, color]) => ({
      key: k,
      label,
      color,
      ap: data.pr_curve[k].average_precision,
      points: data.pr_curve[k].points
        .filter((p) => p.precision != null)
        .map((p) => ({ recall: p.recall, precision: p.precision }))
        .sort((a, b) => a.recall - b.recall),
    }));
  }, [data]);

  if (error) return <div className="alert error">{error.message}</div>;
  if (!data) return <p className="muted">Loading evaluation…</p>;

  const s = data.summary;
  const im = s.identity_matcher;
  const queue = data.review_queue.identity_matcher;
  const rec = data.review_queue.recommended_er_threshold ?? im.recommended_threshold;
  const idx = qi ?? Math.max(0, queue.findIndex((q) => q.threshold === rec));
  const q = queue[idx];
  const calib = data.calibration_bins.calibrated.uniform_bins
    .filter((b) => b.n > 0)
    .map((b) => ({ x: b.mean_predicted, y: b.observed_rate, n: b.n }));
  const held = Object.entries(data.heldout_alias_kind.groups);

  return (
    <section className="eval">
      <div className="alert warn">
        Every number here comes from <strong>synthetic data</strong>. It measures how well the model recovers the error
        patterns the generator injects, not real-world payout accuracy. The model is advisory: it never moves money.
      </div>

      <div className="eval-grid">
        <div className="card">
          <h3>
            Identity matching: precision vs recall <Synthetic />
          </h3>
          <ResponsiveContainer width="100%" height={300}>
            <LineChart margin={{ top: 8, right: 16, bottom: 24, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="recall" type="number" domain={[0, 1]} label={{ value: "recall", position: "insideBottom", offset: -12 }} />
              <YAxis type="number" domain={[0, 1]} label={{ value: "precision", angle: -90, position: "insideLeft" }} />
              <Tooltip formatter={(v) => Number(v).toFixed(3)} labelFormatter={(l) => `recall ${Number(l).toFixed(3)}`} />
              <Legend verticalAlign="top" />
              {pr.map((sr) => (
                <Line key={sr.key} data={sr.points} dataKey="precision" name={`${sr.label} (AP ${sr.ap.toFixed(3)})`}
                  stroke={sr.color} dot={false} strokeWidth={2} isAnimationActive={false} />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>

        <div className="card">
          <h3>
            Calibration (calibrated matcher, 10 equal-width bins) <Synthetic />
          </h3>
          <ResponsiveContainer width="100%" height={300}>
            <ScatterChart margin={{ top: 8, right: 16, bottom: 24, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="x" type="number" domain={[0, 1]} name="predicted" label={{ value: "predicted probability", position: "insideBottom", offset: -12 }} />
              <YAxis dataKey="y" type="number" domain={[0, 1]} name="observed" label={{ value: "observed rate", angle: -90, position: "insideLeft" }} />
              <ZAxis dataKey="n" range={[30, 200]} name="pairs" />
              <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke="#9ca3af" strokeDasharray="4 4" />
              <Tooltip formatter={(v) => (typeof v === "number" ? Number(v).toFixed(3) : v)} />
              <Scatter data={calib} fill="#2563eb" isAnimationActive={false} />
            </ScatterChart>
          </ResponsiveContainer>
          <p className="small muted">
            ECE {data.calibration_bins.calibrated.ece} (raw {data.calibration_bins.raw.ece}), method{" "}
            {data.calibration_bins.calibration_method?.method}. Dashed line = perfect calibration. Dot size = pairs in the
            bin; the middle bins hold only 3–12 pairs each, so their scatter is noise.
          </p>
        </div>
      </div>

      <div className="card">
        <h3>
          Review queue at a chosen threshold <Synthetic />
        </h3>
        <label className="slider">
          match threshold <strong>{q.threshold}</strong>
          {q.threshold === rec && <span className="muted"> (operating point)</span>}
          <input type="range" min={0} max={queue.length - 1} step={1} value={idx} onChange={(e) => setQi(Number(e.target.value))} />
        </label>
        <div className="stats">
          <div><b>{q.flagged_pairs}</b><span>flagged pairs</span></div>
          <div><b>{q.flagged_rows_per_1000.toFixed(0)}</b><span>flagged rows per 1000</span></div>
          <div><b>{q.flagged_rows_new_record_per_1000.toFixed(0)}</b><span>involving a never-paid record, per 1000</span></div>
          <div><b>{pct(q.pair_precision)}</b><span>pair precision</span></div>
          <div><b>{pct(q.pair_recall)}</b><span>pair recall</span></div>
          <div>
            <b>{q.incident_recall.alias_duplicate.caught}/{q.incident_recall.alias_duplicate.n}</b>
            <span>alias duplicates caught (95% CI {ci(q.incident_recall.alias_duplicate.recall_ci95)})</span>
          </div>
          <div>
            <b>{q.incident_recall.email_typo.caught}/{q.incident_recall.email_typo.n}</b>
            <span>email typos caught (95% CI {ci(q.incident_recall.email_typo.recall_ci95)})</span>
          </div>
        </div>
      </div>

      <div className="eval-grid">
        <div className="card">
          <h3>
            Headline numbers <Synthetic />
          </h3>
          <table className="table compact">
            <tbody>
              <tr><td>Average precision: matcher</td><td className="num">{im.average_precision.matcher}</td></tr>
              <tr><td>Average precision: logistic regression, same features</td><td className="num">{im.average_precision.baseline_logreg_same_features}</td></tr>
              <tr><td>Average precision: rapidfuzz name + email</td><td className="num">{im.average_precision.baseline_name_plus_email}</td></tr>
              <tr><td>Average precision without currency/amount features</td><td className="num">{im.ablation_no_context_features.average_precision}</td></tr>
              <tr><td>Exact-duplicate rule</td><td className="num">{s.exact_duplicate_rule.caught}/{s.exact_duplicate_rule.injected_rows}</td></tr>
              <tr><td>Amount anomaly PR-AUC: per-payee model / ratio rule</td><td className="num">{s.amount_anomaly.pr_auc.per_payee_model} / {s.amount_anomaly.pr_auc.ratio_to_median_rule}</td></tr>
              <tr><td>Amount anomaly at default z={s.amount_anomaly.at_default.threshold}: precision / recall</td><td className="num">{s.amount_anomaly.at_default.precision} / {s.amount_anomaly.at_default.recall}</td></tr>
            </tbody>
          </table>
        </div>
        <div className="card">
          <h3>
            Leakage check: recall on an alias kind removed from training <Synthetic />
          </h3>
          <table className="table compact">
            <thead>
              <tr><th>Alias kind</th><th className="num">n</th><th className="num">seen</th><th className="num">held out</th></tr>
            </thead>
            <tbody>
              {held.map(([k, g]) => (
                <tr key={k} className={g.heldout.recall < 0.8 ? "row-warn" : ""}>
                  <td>{k.replaceAll("_", " ")}</td>
                  <td className="num">{g.seen.n_pairs}</td>
                  <td className="num">{g.seen.recall.toFixed(2)}</td>
                  <td className="num">{g.heldout.recall.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="small muted">
            The matcher does not generalise to an unseen combined name-and-email change, or to an unseen email change.
            Several cells have n &lt; 30.
          </p>
        </div>
      </div>

      <div className="card">
        <h3>Limitations</h3>
        <ul>
          {s.caveats.map((c) => (
            <li key={c}>{c}</li>
          ))}
          <li>
            <code>currency_equal</code> is the second most important feature. The generator derives currency from
            country, so it acts as a stand-in for country, even though the scoring contract has no country column.
          </li>
          <li>
            In hard mode, held-out recall drops to 0.10 for email typos and 0.26 for combined name and email changes
            (see ml/README).
          </li>
        </ul>
      </div>
    </section>
  );
}
