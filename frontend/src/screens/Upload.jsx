
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
    <section className="upload-page">
      <div className="upload-header">
        <div>
          <h2>Upload payout batch</h2>
          <p>
            Import your payout data, validate recipient details and run
            risk analysis before sending payments.
          </p>
        </div>
        <span className="upload-status">
          <span className="upload-status-dot" />
          Secure batch processing
        </span>
      </div>

      <div className="upload-layout">
        <div className="card upload-form-card">
          <div className="upload-card-heading">
            <div>
              <span className="upload-eyebrow">NEW BATCH</span>
              <h3>Import payout files</h3>
              <p className="muted">
                Select the CSV files you want to process.
              </p>
            </div>
            <span className="upload-file-icon">CSV</span>
          </div>

          <form onSubmit={submit} className="stack upload-form">
            <label className="upload-file-field">
              <span className="upload-field-heading">
                Batch CSV <span className="req">Required</span>
              </span>
              <span className="upload-field-description">
                Contains the payout transactions to be reviewed.
              </span>
              <input
                type="file"
                accept=".csv,text/csv"
                onChange={(e) => setFile(e.target.files[0] || null)}
              />
              {file && (
                <span className="upload-selected-file">
                  Selected: {file.name}
                </span>
              )}
            </label>

            <label className="upload-file-field">
              <span className="upload-field-heading">
                Payout history <span className="muted">Optional</span>
              </span>
              <span className="upload-field-description">
                Helps the model compare recipients against known payees.
              </span>
              <input
                type="file"
                accept=".csv,text/csv"
                onChange={(e) => setHistory(e.target.files[0] || null)}
              />
              {history && (
                <span className="upload-selected-file">
                  Selected: {history.name}
                </span>
              )}
            </label>

            <div className="upload-submit-area">
              <button className="primary" disabled={!file || busy}>
                {busy ? "Scoring..." : "Upload and score"}
              </button>
              <span className="muted">
                {busy
                  ? "Processing your batch..."
                  : "Your batch will be validated before scoring."}
              </span>
            </div>
          </form>
        </div>

        <aside className="card upload-info-card">
          <span className="upload-eyebrow">BEFORE YOU UPLOAD</span>
          <h3>CSV requirements</h3>
          <p className="muted">
            Make sure your batch file follows the expected format.
          </p>

          <div className="upload-requirement">
            <span className="upload-requirement-number">01</span>
            <div>
              <strong>Required columns</strong>
              <code>{COLUMNS}</code>
            </div>
          </div>

          <div className="upload-requirement">
            <span className="upload-requirement-number">02</span>
            <div>
              <strong>Amount formatting</strong>
              <p>Amounts must contain no more than 2 decimal places.</p>
            </div>
          </div>

          <div className="upload-requirement">
            <span className="upload-requirement-number">03</span>
            <div>
              <strong>Validation first</strong>
              <p>
                If any row is invalid, the batch is rejected before scoring.
              </p>
            </div>
          </div>

          <div className="upload-info-note">
            <strong>Why payout history?</strong>
            <p>
              Providing historical payout data gives the model known
              recipients to compare against during risk analysis.
            </p>
          </div>
        </aside>
      </div>

      {busy && (
        <div className="alert info upload-progress">
          Scoring can take a few seconds on large histories. Please keep this
          page open.
        </div>
      )}

      {error && !rowErrors && (
        <div className="alert error upload-error">{error.message}</div>
      )}

      {rowErrors && (
        <div className="alert error upload-error">
          <strong>{error.detail.message}</strong>
          <div className="upload-error-table">
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
        </div>
      )}
    </section>
  );
}
