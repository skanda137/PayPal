import { Fragment, useEffect, useState } from "react";
import { api } from "../api.js";

function summary(e) {
  const p = e.payload;
  switch (e.kind) {
    case "upload":
      return `${p.n_rows} rows uploaded (${p.filename})${p.n_history ? `, ${p.n_history} history rows` : ""}`;
    case "score":
      return `scored with model ${p.model_version} at threshold ${p.er_threshold}: ${p.actions.proceed} proceed, ${p.actions.review} review, ${p.actions.hold} hold`;
    case "decision":
      return `${p.row_id}: ${p.from ?? "undecided"} → ${p.to}`;
    case "override":
      return `OVERRIDE ${p.row_id}: approved a row the model held (${p.flag_types.join(", ")})`;
    case "payout_request":
      return `payout request ${p.sender_batch_id} with ${p.items.length} items`;
    case "payout_response":
      return `PayPal accepted: batch ${p.batch_header?.payout_batch_id} (${p.batch_header?.batch_status})`;
    case "payout_error":
      return `PayPal error ${p.status ?? ""} ${p.name}: ${p.message}`;
    case "status_change":
      return p.row_id ? `${p.row_id}: ${p.from ?? "—"} → ${p.to}` : `batch: ${p.batch_status_from ?? "—"} → ${p.to}`;
    default:
      return "";
  }
}

export default function Audit({ batchId }) {
  const [events, setEvents] = useState(null);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(null);

  const load = () => api.audit(batchId).then(setEvents, setError);
  useEffect(() => {
    load();
  }, [batchId]); // eslint-disable-line react-hooks/exhaustive-deps

  if (error) return <div className="alert error">{error.message}</div>;
  if (!events) return <p className="muted">Loading…</p>;
  return (
    <section>
      <h2>Audit log</h2>
      <p className="muted">Append-only: the database rejects updates and deletes on this table.</p>
      <button onClick={load}>Refresh</button>
      <table className="table">
        <thead>
          <tr>
            <th>#</th>
            <th>Time (UTC)</th>
            <th>Event</th>
            <th>What happened</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <Fragment key={e.id}>
              <tr className={e.kind === "override" ? "row-override" : ""} onClick={() => setOpen(open === e.id ? null : e.id)}>
                <td>{e.id}</td>
                <td className="mono small">{e.ts.replace("T", " ").slice(0, 23)}</td>
                <td>
                  <span className={`badge kind-${e.kind}`}>{e.kind}</span>
                </td>
                <td>{summary(e)}</td>
              </tr>
              {open === e.id && (
                <tr>
                  <td colSpan={4}>
                    <pre className="raw">{JSON.stringify(e.payload, null, 2)}</pre>
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
      <p className="muted small">Click a row to see the raw payload.</p>
    </section>
  );
}
