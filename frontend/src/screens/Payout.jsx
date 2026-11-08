import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { itemStatus } from "../labels.js";

const POLL_MS = 3000;

export default function Payout({ batchId }) {
  const [payout, setPayout] = useState(null);
  const [problem, setProblem] = useState(null); // {status, message, last_error}
  const [rowsById, setRowsById] = useState(new Map());
  const [retrying, setRetrying] = useState(false);
  const timer = useRef(null);

  const poll = useCallback(async () => {
    clearTimeout(timer.current);
    try {
      const p = await api.payout(batchId);
      setPayout(p);
      setProblem(null);
      if (!p.terminal) timer.current = setTimeout(poll, POLL_MS);
    } catch (e) {
      setProblem({ status: e.status, message: e.message, last_error: e.detail?.last_error, paypal: e.detail?.paypal });
      if (e.status === 502 || e.status === 0) timer.current = setTimeout(poll, POLL_MS * 2); // transient: keep trying
    }
  }, [batchId]);

  useEffect(() => {
    api.batch(batchId).then((b) => setRowsById(new Map(b.rows.map((r) => [r.row_id, r]))), () => {});
    poll();
    return () => clearTimeout(timer.current);
  }, [batchId, poll]);

  async function retrySend() {
    setRetrying(true);
    try {
      await api.send(batchId); // same sender_batch_id; the backend never creates a second batch
      await poll();
    } catch (e) {
      setProblem({ status: e.status, message: e.message, paypal: e.detail?.paypal });
    } finally {
      setRetrying(false);
    }
  }

  if (problem?.status === 404) {
    return (
      <p className="empty">
        No payout has been sent for this batch yet. Approve rows on the <a href={`#tab=review&batch=${batchId}`}>Review</a> screen
        and press Send.
      </p>
    );
  }

  return (
    <section>
      <h2>Payout status</h2>
      {problem && (
        <div className="alert error">
          {problem.message}
          {(problem.paypal || problem.last_error) && (
            <div className="small">
              PayPal: {(problem.paypal || problem.last_error).name} — {(problem.paypal || problem.last_error).message}{" "}
              (debug_id {(problem.paypal || problem.last_error).debug_id ?? "n/a"})
            </div>
          )}
          {problem.status === 409 && (
            <div>
              <button className="primary" onClick={retrySend} disabled={retrying}>
                {retrying ? "Retrying…" : "Retry send (same batch id)"}
              </button>
            </div>
          )}
        </div>
      )}
      {!payout && !problem && <p className="muted">Loading…</p>}
      {payout && (
        <>
          <div className="chips">
            <span className={`chip status-${payout.terminal ? "done" : "live"}`}>
              batch status: <strong>{payout.batch_status ?? "unknown"}</strong>
              {!payout.terminal && " · refreshing every 3 s"}
            </span>
            <span className="chip">PayPal batch {payout.payout_batch_id}</span>
            <span className="chip mono">sender_batch_id {payout.sender_batch_id}</span>
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>Row</th>
                <th>Name</th>
                <th>Email</th>
                <th className="num">Amount</th>
                <th>Status</th>
                <th>PayPal error</th>
              </tr>
            </thead>
            <tbody>
              {payout.items.map((it) => {
                const r = rowsById.get(it.row_id);
                const [label, tone] = itemStatus(it.transaction_status);
                return (
                  <tr key={it.row_id}>
                    <td>{it.row_id}</td>
                    <td>{r?.recipient_name}</td>
                    <td>{r?.receiver_email}</td>
                    <td className="num">{r && `${r.amount} ${r.currency}`}</td>
                    <td>
                      <span className={`status ${tone}`}>{it.transaction_status ?? "—"}</span> {label}
                    </td>
                    <td className="small">{it.errors ? `${it.errors.name}: ${it.errors.message}` : ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <button onClick={poll}>Refresh now</button>
        </>
      )}
    </section>
  );
}
