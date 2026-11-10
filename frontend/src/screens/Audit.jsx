import { useEffect, useMemo, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import { api } from "../api.js";
import { gridDefaults } from "../grid.js";

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
      return `PayPal accepted the request: batch ${p.batch_header?.payout_batch_id} (${p.batch_header?.batch_status})`;
    case "payout_error":
      return `PayPal error ${p.status ?? ""} ${p.name}: ${p.message}`;
    case "status_change":
      return p.row_id ? `${p.row_id}: ${p.from ?? "—"} → ${p.to}` : `batch: ${p.batch_status_from ?? "—"} → ${p.to}`;
    default:
      return "";
  }
}

const KindPill = ({ value }) => <span className={`badge kind-${value}`}>{value}</span>;

export default function Audit({ batchId }) {
  const [events, setEvents] = useState(null);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(null);

  const load = () => api.audit(batchId).then(setEvents, setError);
  useEffect(() => {
    load();
  }, [batchId]); // eslint-disable-line react-hooks/exhaustive-deps

  const columnDefs = useMemo(
    () => [
      { field: "id", headerName: "#", width: 70, pinned: "left" },
      { colId: "ts", headerName: "Time (UTC)", width: 200, valueGetter: (p) => p.data.ts.replace("T", " ").slice(0, 23), cellClass: "mono" },
      { field: "kind", headerName: "Event", width: 160, cellRenderer: KindPill, filter: "agTextColumnFilter" },
      {
        colId: "what",
        headerName: "What happened",
        flex: 1,
        minWidth: 300,
        valueGetter: (p) => summary(p.data),
        filter: "agTextColumnFilter",
        tooltipValueGetter: (p) => p.value,
      },
    ],
    [],
  );

  if (error) return <div className="alert error">{error.message}</div>;
  if (!events) return <p className="muted">Loading…</p>;
  const shown = events.find((e) => e.id === open);
  return (
    <section>
      <h2>Audit log</h2>
      <p className="muted">Append-only: the database rejects updates and deletes on this table. Click a row to see the raw payload.</p>
      <button onClick={load}>Refresh</button>
      <div className={`grid-auto ${events.length > 15 ? "tall" : ""}`}>
        <AgGridReact
          {...gridDefaults}
          rowData={events}
          columnDefs={columnDefs}
          getRowId={(p) => String(p.data.id)}
          domLayout={events.length > 15 ? "normal" : "autoHeight"}
          onRowClicked={(e) => setOpen(open === e.data.id ? null : e.data.id)}
          rowClassRules={{ "row-hold": (p) => p.data.kind === "override" || p.data.kind === "payout_error" }}
        />
      </div>
      {shown && (
        <div className="card">
          <h3>
            Event #{shown.id} <KindPill value={shown.kind} />
          </h3>
          <pre className="raw">{JSON.stringify(shown.payload, null, 2)}</pre>
        </div>
      )}
    </section>
  );
}
