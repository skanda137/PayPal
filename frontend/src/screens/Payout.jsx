import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import { api } from "../api.js";
import { gridDefaults } from "../grid.js";
import { itemStatus } from "../labels.js";

const POLL_MS = 3000;
const STILL_MOVING = new Set([null, "PENDING"]);
const TONE_RANK = { bad: 0, warn: 1, neutral: 2, good: 3 }; // items that did not arrive come first

function StatusPill({ value }) {
  const [, tone] = itemStatus(value);
  return <span className={`status ${tone}`}>{value ?? "—"}</span>;
}

export default function Payout({ batchId }) {
  const [payout, setPayout] = useState(null);
  const [problem, setProblem] = useState(null); // {status, message, last_error}
  const [rows, setRows] = useState(null); // the grid's first rows; later changes go in as row transactions
  const [retrying, setRetrying] = useState(false);
  const timer = useRef(null);
  const grid = useRef(null);
  const info = useRef(new Map()); // row_id -> uploaded row (name, email, amount)
  const shown = useRef(null); // row_id -> status text currently in the grid

  const toRow = (it) => {
    const r = info.current.get(it.row_id);
    return {
      row_id: it.row_id,
      recipient_name: r?.recipient_name,
      receiver_email: r?.receiver_email,
      amount: r ? `${r.amount} ${r.currency}` : "",
      transaction_status: it.transaction_status,
      meaning: itemStatus(it.transaction_status)[0],
      error: it.errors ? `${it.errors.name}: ${it.errors.message}` : "",
    };
  };

  const poll = useCallback(async () => {
    clearTimeout(timer.current);
    try {
      const p = await api.payout(batchId);
      setPayout(p);
      setProblem(null);
      const data = p.items.map(toRow);
      const sig = (d) => `${d.transaction_status}|${d.error}`;
      if (!shown.current || !grid.current?.api) {
        setRows(data);
      } else {
        // Only the items whose status changed are updated, so the grid flashes exactly those cells.
        const changed = data.filter((d) => shown.current.get(d.row_id) !== sig(d));
        if (changed.length) grid.current.api.applyTransaction({ update: changed });
      }
      shown.current = new Map(data.map((d) => [d.row_id, sig(d)]));
      const moving = !p.terminal || p.items.some((i) => STILL_MOVING.has(i.transaction_status));
      if (moving) timer.current = setTimeout(poll, POLL_MS);
    } catch (e) {
      setProblem({ status: e.status, message: e.message, last_error: e.detail?.last_error, paypal: e.detail?.paypal });
      if (e.status === 502 || e.status === 0) timer.current = setTimeout(poll, POLL_MS * 2); // transient: keep trying
    }
  }, [batchId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    shown.current = null;
    setRows(null);
    api.batch(batchId).then(
      (b) => {
        info.current = new Map(b.rows.map((r) => [r.row_id, r]));
        poll();
      },
      (e) => setProblem({ status: e.status, message: e.message }),
    );
    return () => clearTimeout(timer.current);
  }, [batchId, poll]);

  const columnDefs = useMemo(
    () => [
      { field: "row_id", headerName: "Row", width: 84, pinned: "left" },
      { field: "recipient_name", headerName: "Name", flex: 1, minWidth: 130, tooltipField: "recipient_name" },
      { field: "receiver_email", headerName: "Email", flex: 1.3, minWidth: 180, tooltipField: "receiver_email" },
      { field: "amount", headerName: "Amount", width: 122, type: "rightAligned", sortable: false },
      { field: "transaction_status", headerName: "PayPal status",
        width: 130,
        cellRenderer: StatusPill,
        enableCellChangeFlash: true,
        comparator: (a, b) => TONE_RANK[itemStatus(a)[1]] - TONE_RANK[itemStatus(b)[1]],
        sort: "asc",
      },
      {
        field: "meaning",
        headerName: "What it means",
        flex: 2.2,
        minWidth: 260,
        wrapText: true,
        autoHeight: true,
        enableCellChangeFlash: true,
        cellClassRules: { "cell-bad": (p) => itemStatus(p.data.transaction_status)[1] === "bad" },
      },
      { field: "error", headerName: "PayPal error", flex: 1.6, minWidth: 200, wrapText: true, autoHeight: true, enableCellChangeFlash: true },
    ],
    [],
  );

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

  const items = payout?.items ?? [];
  const delivered = items.filter((i) => i.transaction_status === "SUCCESS").length;
  const waiting = items.filter((i) => STILL_MOVING.has(i.transaction_status)).length;
  const notDelivered = items.length - delivered - waiting;

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
            <div className="payout-metrics">
  <div className="metric-card metric-success">
    <span className="metric-label">Delivered</span>
    <strong className="metric-value">{delivered}</strong>
    <span className="metric-caption">Successfully delivered to recipients</span>
  </div>

  <div className="metric-card metric-danger">
    <span className="metric-label">Not delivered</span>
    <strong className="metric-value">{notDelivered}</strong>
    <span className="metric-caption">Failed or unsuccessful payments</span>
  </div>

  <div className="metric-card metric-warning">
    <span className="metric-label">Awaiting confirmation</span>
    <strong className="metric-value">{waiting}</strong>
    <span className="metric-caption">Still processing with PayPal</span>
  </div>

  <div className="metric-card">
    <span className="metric-label">Total payout items</span>
    <strong className="metric-value">{items.length}</strong>
    <span className="metric-caption">Items submitted in this batch</span>
  </div>
</div>

<div className="payout-metadata">
  <div>
    <span className="metadata-label">PayPal batch status</span>
    <strong className="metadata-value">{payout.batch_status ?? "Unknown"}</strong>
    {!payout.terminal && <span className="live-indicator">Refreshing every 3 seconds</span>}
  </div>

  <div>
    <span className="metadata-label">PayPal payout batch ID</span>
    <strong className="metadata-id">{payout.payout_batch_id}</strong>
  </div>

  <div>
    <span className="metadata-label">Sender batch ID</span>
    <strong className="metadata-id">{payout.sender_batch_id}</strong>
  </div>
</div>
          {payout.batch_status === "SUCCESS" && (
            <p className="muted small">
              Batch status SUCCESS means PayPal finished processing the batch. It does not mean every item was delivered: check each
              item below.
            </p>
          )}
          {notDelivered > 0 && (
            <div className="alert error">
              <strong>
                {notDelivered} of {items.length} item{items.length > 1 ? "s" : ""} did not reach the recipient.
              </strong>{" "}
              See "What it means" for each one.
            </div>
          )}
          {rows && (
            <div className={`grid-auto ${rows.length > 15 ? "tall" : ""}`}>
              <AgGridReact
                {...gridDefaults}
                ref={grid}
                rowData={rows}
                columnDefs={columnDefs}
                getRowId={(p) => p.data.row_id}
                domLayout={rows.length > 15 ? "normal" : "autoHeight"}
                rowClassRules={{
                  "row-hold": (p) => itemStatus(p.data.transaction_status)[1] === "bad",
                  "row-review": (p) => itemStatus(p.data.transaction_status)[1] === "warn",
                }}
              />
            </div>
          )}
          <button onClick={poll}>Refresh now</button>
        </>
      )}
    </section>
  );
}
