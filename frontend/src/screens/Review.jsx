import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import { api } from "../api.js";
import Modal from "../Modal.jsx";
import { fmtTotals } from "../money.js";
import { gridDefaults } from "../grid.js";
import { CATEGORY_LABEL, FLAG_LABEL, SHORT_FLAG } from "../labels.js";

const RANK = { hold: 0, review: 1, proceed: 2 }; // flagged rows first
const firstCategory = (r) => r.flags.find((f) => f.evidence?.category)?.evidence.category ?? null;
const flagTypes = (r) => [...new Set(r.flags.map((f) => f.type))];
const flagText = (r) => flagTypes(r).map((t) => FLAG_LABEL[t] || t).join(", ");
// Same order as the grid's default sort: hold, review, proceed; larger amounts first within each group.
const bySeverity = (a, b) => RANK[a.action] - RANK[b.action] || Number(b.amount) - Number(a.amount);

function Badge({ value, kind }) {
  if (!value) return <span className="badge undecided">{kind === "decision" ? "undecided" : "—"}</span>;
  return <span className={`badge ${value}`}>{value}</span>;
}

const ActionPill = (p) => <Badge value={p.value} />;

function FlagChips({ data }) {
  return (
    <span className="flag-chips">
      {flagTypes(data).map((t) => (
        <span key={t} className={`flag-chip ${t}`}>
          {SHORT_FLAG[t] ?? t}
        </span>
      ))}
    </span>
  );
}

function DecisionCell({ data, context }) {
  return (
    <span className="decision-cell">
      <Badge value={data.decision} kind="decision" />
      {!context.current.locked && <span className="caret">▾</span>}
    </span>
  );
}

export default function Review({ batchId, onSent }) {
  const [batch, setBatch] = useState(null);
  const [error, setError] = useState(null);
  const [actionFilter, setActionFilter] = useState("all");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [detailId, setDetailId] = useState(null);
  const [panelOpen, setPanelOpen] = useState(true);
  const [selected, setSelected] = useState([]);
  const [modal, setModal] = useState(null); // {kind:'override', ids, rows} | {kind:'send'}
  const [busy, setBusy] = useState(false);
  const [modalError, setModalError] = useState(null);
  const gridRef = useRef(null);
  const filters = useRef({ action: "all", category: "all" });

  useEffect(() => {
    let live = true;
    setBatch(null);
    api.batch(batchId).then(
      (b) => {
        if (!live) return;
        setBatch(b);
        // Open the side panel on the most severe flagged row.
        setDetailId([...b.rows].sort(bySeverity).find((r) => r.action !== "proceed")?.row_id ?? null);
      },
      (e) => live && setError(e),
    );
    return () => {
      live = false;
    };
  }, [batchId]);

  const byId = useMemo(() => new Map((batch?.rows || []).map((r) => [r.row_id, r])), [batch]);
  const locked = !!batch?.summary.locked;

  const apply = useCallback(
    async (ids, decision, override) => {
      const res = await api.decide(batchId, Object.fromEntries(ids.map((id) => [id, decision])), override);
      setBatch(res);
    },
    [batchId],
  );

  const decide = useCallback(
    async (ids, decision) => {
      setError(null);
      ids = ids.filter((id) => byId.get(id) && byId.get(id).decision !== decision);
      if (!ids.length) return;
      const needOverride = ids.filter((id) => decision === "approve" && byId.get(id).action === "hold");
      if (needOverride.length) {
        setModalError(null);
        setModal({ kind: "override", ids, rows: needOverride.map((id) => byId.get(id)) });
        return;
      }
      try {
        await apply(ids, decision, false);
      } catch (e) {
        setError(e);
      }
    },
    [apply, byId],
  );
  // The grid keeps the first callbacks it was given, so column code reads the current ones from this ref.
  const live = useRef({});
  live.current = { decide, locked };

  // Toolbar filters run inside the grid (external filter), so the CSV export can still reach every row.
  useEffect(() => {
    filters.current = { action: actionFilter, category: categoryFilter };
    gridRef.current?.api?.onFilterChanged();
  }, [actionFilter, categoryFilter]);

  const columnDefs = useMemo(
    () => [
      { field: "row_id", headerName: "Row", width: 72, pinned: "left" },
      { field: "recipient_name", headerName: "Name", flex: 1, minWidth: 138, filter: "agTextColumnFilter", tooltipField: "recipient_name" },
      { field: "receiver_email", headerName: "Email", flex: 1.6, minWidth: 220, filter: "agTextColumnFilter", tooltipField: "receiver_email" },
      {
        colId: "amount",
        headerName: "Amount",
        width: 132,
        type: "rightAligned",
        filter: "agNumberColumnFilter",
        valueGetter: (p) => Number(p.data.amount), // for sorting and filtering only; the text shown is the exact string
        valueFormatter: (p) => `${p.data.amount} ${p.data.currency}`,
        sort: "desc",
        sortIndex: 1,
      },
      {
        field: "action",
        headerName: "Model",
        width: 104,
        cellRenderer: ActionPill,
        comparator: (a, b) => RANK[a] - RANK[b],
        sort: "asc",
        sortIndex: 0,
      },
      {
        colId: "flags",
        headerName: "Flags",
        width: 124,
        sortable: false,
        filter: "agTextColumnFilter",
        valueGetter: (p) => flagText(p.data),
        cellRenderer: FlagChips,
        tooltipValueGetter: (p) => p.value,
      },
      {
        colId: "decision",
        headerName: "Decision",
        width: 124,
        sortable: false,
        pinned: "right",
        valueGetter: (p) => p.data.decision ?? "undecided",
        cellRenderer: DecisionCell,
        editable: () => !live.current.locked,
        singleClickEdit: true,
        cellEditor: "agSelectCellEditor",
        cellEditorParams: { values: ["undecided", "approve", "hold"] },
        valueSetter: (p) => {
          // The server owns decisions: ask it, and let the refreshed batch redraw the cell.
          if (p.newValue === "approve" || p.newValue === "hold") live.current.decide([p.data.row_id], p.newValue);
          return false;
        },
      },
    ],
    [],
  );

  const defaultColDef = useMemo(
    () => ({
      ...gridDefaults.defaultColDef,
      // A and H are row shortcuts, so they must not start a cell edit.
      suppressKeyboardEvent: (p) => !p.editing && !p.event.ctrlKey && !p.event.metaKey && /^[ah]$/i.test(p.event.key),
    }),
    [],
  );

  // Stable props: the grid redraws rows (and closes an open cell editor) when these change identity.
  const gridProps = useMemo(
    () => ({
      getRowId: (p) => p.data.row_id,
      isExternalFilterPresent: () => filters.current.action !== "all" || filters.current.category !== "all",
      doesExternalFilterPass: (node) =>
        (filters.current.action === "all" || node.data.action === filters.current.action) &&
        (filters.current.category === "all" || (firstCategory(node.data) ?? "none") === filters.current.category),
      rowSelection: { mode: "multiRow", enableClickSelection: false },
      selectionColumnDef: { pinned: "left", width: 40 },
      onSelectionChanged: (e) => setSelected(e.api.getSelectedRows().map((r) => r.row_id)),
      onRowClicked: (e) => {
        setDetailId(e.data.row_id);
        setPanelOpen(true);
      },
      rowClassRules: { "row-hold": (p) => p.data.action === "hold", "row-review": (p) => p.data.action === "review" },
      overlayNoRowsTemplate: "No rows match these filters.",
    }),
    [],
  );

  const onCellKeyDown = useCallback((e) => {
    const k = e.event;
    if (!k || k.ctrlKey || k.metaKey || k.altKey || live.current.locked) return;
    if (gridRef.current.api.getEditingCells().length) return;
    const key = k.key.toLowerCase();
    if (key === "a") live.current.decide([e.data.row_id], "approve");
    if (key === "h") live.current.decide([e.data.row_id], "hold");
  }, []);

  if (error && !batch) return <div className="alert error">{error.message}</div>;
  if (!batch) return <p className="muted">Loading batch…</p>;

  const s = batch.summary;
  const approved = batch.rows.filter((r) => r.decision === "approve");
  const notSent = batch.rows.filter((r) => r.decision === "hold").sort(bySeverity);
  const undecided = batch.rows.filter((r) => r.decision == null);
  const overrides = approved.filter((r) => r.action === "hold");
  const reviewedOk = approved.filter((r) => r.action === "review");
  const flagged = batch.rows.filter((r) => r.action !== "proceed");
  const held = batch.rows.filter((r) => r.action === "hold");
  const detail = detailId ? byId.get(detailId) : null;
  const categories = [...new Set(batch.rows.map((r) => firstCategory(r) ?? "none"))];
  const canSend = !locked && approved.length > 0 && undecided.length === 0;

  async function confirmOverride() {
    setBusy(true);
    setModalError(null);
    try {
      await apply(modal.ids, "approve", true);
      setModal(null);
    } catch (e) {
      setModalError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function confirmSend() {
    setBusy(true);
    setModalError(null);
    try {
      await api.send(batchId);
      setModal(null);
      onSent();
    } catch (e) {
      setModalError(e.detail?.paypal ? `${e.detail.message}: ${e.detail.paypal.name} ${e.detail.paypal.message} (debug_id ${e.detail.paypal.debug_id})` : e.message);
    } finally {
      setBusy(false);
    }
  }

  function exportHeld() {
    gridRef.current.api.exportDataAsCsv({
      fileName: `payoutguard-${batchId}-held-rows.csv`,
      exportedRows: "all", // ignore the on-screen filters: the file lists every held row
      columnKeys: ["row_id", "recipient_name", "receiver_email", "amount", "action", "flags", "decision"],
      shouldRowBeSkipped: (p) => p.node.data.decision !== "hold",
      processCellCallback: (p) => (p.column.getColId() === "amount" ? `${p.node.data.amount} ${p.node.data.currency}` : p.value),
    });
  }

  return (
    <section>
      <div className="chips">
        <span className="chip">{s.n_rows} rows</span>
        <span className="chip proceed">{s.actions.proceed} proceed</span>
        <span className="chip review">{s.actions.review} review</span>
        <span className="chip hold">{s.actions.hold} hold</span>
        <span className="chip">
          decisions: {s.decisions.approve} approve · {s.decisions.hold} hold · {s.decisions.undecided} undecided
        </span>
        <span className="chip" title="Sum of amounts on rows the model flagged. Demo batches use synthetic data.">
          Money at risk (flagged): {fmtTotals(flagged)} <em>synthetic data</em>
        </span>
        <span className="chip">held by model: {fmtTotals(held)}</span>
      </div>
      <p className="muted small">
        model {s.model_version} · match threshold {s.er_threshold} · amount z threshold {s.z_threshold} · history rows {s.n_history}
      </p>

      {locked && (
        <div className="alert info">
          A payout was started for this batch, so decisions are locked. <a href={`#tab=payout&batch=${batchId}`}>See payout status</a>
        </div>
      )}
      {error && <div className="alert error">{error.message}</div>}

      <div className="toolbar">
        <label>
          Search
          <input type="search" value={search} placeholder="name, email, row, flag" onChange={(e) => setSearch(e.target.value)} />
        </label>
        <label>
          Action
          <select value={actionFilter} onChange={(e) => setActionFilter(e.target.value)}>
            <option value="all">all</option>
            <option value="proceed">proceed</option>
            <option value="review">review</option>
            <option value="hold">hold</option>
          </select>
        </label>
        <label>
          Category
          <select value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)}>
            <option value="all">all</option>
            {categories.map((c) => (
              <option key={c} value={c}>
                {c === "none" ? "no category" : CATEGORY_LABEL[c] || c}
              </option>
            ))}
          </select>
        </label>
        <span className="spacer" />
        <button disabled={locked || !selected.length} onClick={() => decide(selected, "approve")}>
          Approve selected ({selected.length})
        </button>
        <button disabled={locked || !selected.length} onClick={() => decide(selected, "hold")}>
          Hold selected ({selected.length})
        </button>
        <button disabled={!notSent.length} onClick={exportHeld} title="CSV of every row whose decision is hold">
          Export held rows ({notSent.length})
        </button>
        <button onClick={() => setPanelOpen(!panelOpen)}>{panelOpen ? "Hide details" : "Show details"}</button>
        <button
          className="primary"
          disabled={!canSend}
          onClick={() => {
            setModalError(null);
            setModal({ kind: "send" });
          }}
        >
          Send approved ({approved.length})
        </button>
      </div>
      <p className="muted small hint">
        {!locked && undecided.length > 0 && (
          <strong className="warn-text">
            Send is disabled: {undecided.length} flagged row{undecided.length > 1 ? "s are" : " is"} still undecided.{" "}
          </strong>
        )}
        Click a Decision cell to change it, or focus a row and press <kbd>A</kbd> to approve or <kbd>H</kbd> to hold.
      </p>

      {/* Highlight for the row shown in the side panel. Plain CSS, so the grid never has to redraw a row for it. */}
      {detailId && <style>{`.grid-wrap .ag-row[row-id="${CSS.escape(detailId)}"] .ag-cell { background: var(--info-bg); }`}</style>}
      <div className={`review-layout ${panelOpen ? "" : "no-panel"}`}>
        <div className="grid-wrap">
          <AgGridReact
            {...gridDefaults}
            ref={gridRef}
            rowData={batch.rows}
            columnDefs={columnDefs}
            defaultColDef={defaultColDef}
            {...gridProps}
            context={live}
            quickFilterText={search}
            onCellKeyDown={onCellKeyDown}
          />
        </div>
        {panelOpen && (
          <aside className="detail card">
            {!detail && <p className="muted">Click a row to see why it was flagged.</p>}
            {detail && (
              <>
                <h3>
                  {detail.recipient_name} <span className="muted small">{detail.row_id}</span>
                </h3>
                <p className="muted wrap">
                  {detail.receiver_email} · {detail.amount} {detail.currency}
                </p>
                <p>
                  Model says <Badge value={detail.action} /> · decision <Badge value={detail.decision} kind="decision" />
                </p>
                {!detail.flags.length && <p className="muted">No flags. This row starts approved.</p>}
                {detail.flags.map((f, i) => (
                  <div key={i} className="flag">
                    <div className="flag-head">
                      <strong>{FLAG_LABEL[f.type] || f.type}</strong>
                      {f.prob != null && <span className="muted"> · match probability {(f.prob * 100).toFixed(0)}%</span>}
                    </div>
                    {f.evidence?.category && <div className="small">{CATEGORY_LABEL[f.evidence.category]}</div>}
                    <p>{f.explanation}</p>
                    {!!f.linked_rows?.length && (
                      <div className="small">
                        Linked rows:{" "}
                        {f.linked_rows.map((id) =>
                          byId.has(id) ? (
                            <button key={id} className="link" onClick={() => setDetailId(id)}>
                              {id}
                            </button>
                          ) : (
                            <span key={id} className="linked-hist" title="row in the payout history">
                              {id.replace(/^history:/, "")} (history)
                            </span>
                          ),
                        )}
                      </div>
                    )}
                    {f.evidence?.signals && <div className="small muted">signals: {f.evidence.signals.join(", ")}</div>}
                  </div>
                ))}
              </>
            )}
          </aside>
        )}
      </div>

      {modal?.kind === "override" && (
        <Modal
          title="Override the model's hold?"
          danger
          confirmLabel={`Approve ${modal.rows.length} held row${modal.rows.length > 1 ? "s" : ""}`}
          onCancel={() => setModal(null)}
          onConfirm={confirmOverride}
          busy={busy}
        >
          <p>The model held these rows. Approving them is logged as an override in the audit log.</p>
          <ul className="override-list">
            {modal.rows.map((r) => (
              <li key={r.row_id}>
                <strong>{r.row_id}</strong> {r.recipient_name} &lt;{r.receiver_email}&gt; {r.amount} {r.currency}
                <div className="small muted">{flagText(r)}</div>
                {r.flags[0] && <div className="small">{r.flags[0].explanation}</div>}
              </li>
            ))}
          </ul>
          {modalError && <div className="alert error">{modalError}</div>}
        </Modal>
      )}

      {modal?.kind === "send" && (
        <Modal
          title="Confirm payout"
          confirmLabel={`Send ${approved.length} payout${approved.length > 1 ? "s" : ""}`}
          onCancel={() => setModal(null)}
          onConfirm={confirmSend}
          busy={busy}
        >
          <div className="confirm-total">
            <div>
              <b>{approved.length}</b>
              <span>item{approved.length > 1 ? "s" : ""} to send</span>
            </div>
            <div>
              <b>{fmtTotals(approved)}</b>
              <span>total, PayPal sandbox</span>
            </div>
            <div>
              <b>{notSent.length}</b>
              <span>row{notSent.length === 1 ? "" : "s"} not sent ({fmtTotals(notSent)})</span>
            </div>
          </div>

          {overrides.length > 0 && (
            <div className="alert error">
              <strong>
                {overrides.length} override{overrides.length > 1 ? "s" : ""}: the model held {overrides.length > 1 ? "these rows" : "this row"}, and
                you approved {overrides.length > 1 ? "them" : "it"}.
              </strong>
              <ul className="plain">
                {overrides.map((r) => (
                  <li key={r.row_id}>
                    <strong>{r.row_id}</strong> {r.recipient_name}, {r.amount} {r.currency}: {flagText(r)}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {reviewedOk.length > 0 && (
            <div className="alert warn">
              Approved after review: {reviewedOk.map((r) => `${r.row_id} (${flagText(r)})`).join(", ")}
            </div>
          )}

          <h4>Not sent</h4>
          {notSent.length === 0 && <p className="muted">Every row is being sent.</p>}
          {notSent.length > 0 && (
            <div className="not-sent">
              <table className="table compact">
                <thead>
                  <tr>
                    <th>Row</th>
                    <th>Name</th>
                    <th className="num">Amount</th>
                    <th>Why</th>
                  </tr>
                </thead>
                <tbody>
                  {notSent.map((r) => (
                    <tr key={r.row_id}>
                      <td>{r.row_id}</td>
                      <td>{r.recipient_name}</td>
                      <td className="num">
                        {r.amount} {r.currency}
                      </td>
                      <td>
                        Held by you.{" "}
                        {r.action === "proceed" ? "The model raised no flag." : `Model: ${r.action} (${flagText(r)}).`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="muted small">After this, decisions are locked.</p>
          {modalError && <div className="alert error">{modalError}</div>}
        </Modal>
      )}
    </section>
  );
}
