import { useState } from "react";
import { api } from "../api.js";

const COLUMNS = "row_id, sender_item_id, receiver_email, recipient_name, amount, currency, note, date";

export default function Upload({ onUploaded }) {
  const [file, setFile] = useState(null);
  const [history, setHistory] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await api.upload(file, history);
      onUploaded(res.batch_id);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const rowErrors = error?.detail?.errors;

  return (
    <section className="card narrow">
      <h2>Upload a payout batch</h2>
      <p className="muted">
        CSV with exactly these columns: <code>{COLUMNS}</code>. Amounts need at most 2 decimals. If any row is
        invalid, nothing is scored.
      </p>
      <form onSubmit={submit} className="stack">
        <label>
          Batch CSV <span className="req">required</span>
          <input type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files[0] || null)} />
        </label>
        <label>
          Payout history CSV <span className="muted">optional; gives the model known payees to compare against</span>
          <input type="file" accept=".csv,text/csv" onChange={(e) => setHistory(e.target.files[0] || null)} />
        </label>
        <div>
          <button className="primary" disabled={!file || busy}>
            {busy ? "Scoring…" : "Upload and score"}
          </button>
        </div>
      </form>
      {busy && <p className="muted">Scoring can take a few seconds on large histories.</p>}
      {error && !rowErrors && <div className="alert error">{error.message}</div>}
      {rowErrors && (
        <div className="alert error">
          <strong>{error.detail.message}</strong>
          <table className="table compact">
            <thead>
              <tr>
                <th>File</th>
                <th>Line</th>
                <th>Row</th>
                <th>Field</th>
                <th>Problem</th>
              </tr>
            </thead>
            <tbody>
              {rowErrors.map((e, i) => (
                <tr key={i}>
                  <td>{e.file}</td>
                  <td>{e.line ?? "—"}</td>
                  <td>{e.row_id ?? "—"}</td>
                  <td>{e.field ?? "—"}</td>
                  <td>{e.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
