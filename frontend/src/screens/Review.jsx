import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import { api } from "../api.js";
import Modal from "../Modal.jsx";
import { fmtTotals } from "../money.js";
import { CATEGORY_LABEL, FLAG_LABEL } from "../labels.js";

const SHORT_FLAG = { duplicate_payee: "duplicate", typo_receiver: "email typo", amount_anomaly: "amount", invalid_input: "invalid" };
const firstCategory =(r) => r.flags.find((f) => f.evidence?.category)?.evidence.category ?? null;

function Badge({ value, kind }) {
  if (!value) return <span className="badge undecided">{kind === "decision" ? "undecided" : "—"}</span>;
  return <span className={`badge ${value}`}>{value}</span>;
}

function DecisionButtons({ data, context }) {
  const { decide, locked } = context;
  return (
    <span className="row-actions">
      <button
        className="mini approve"
        disabled={locked || data.decision === "approve"}
        onClick={(e) => {
          e.stopPropagation();
          decide([data.row_id], "approve");
        }}
      >
        Approve
      </button>
      <button
        className="mini hold"
        disabled={locked || data.decision === "hold"}
        onClick={(e) => {
          e.stopPropagation();
          decide([data.row_id], "hold");
        }}
      >
        Hold
      </button>
    </span>
  );
}

export default function Review({ batchId, onSent }) {
  const [batch, setBatch] = useState(null);
  const [error, setError] = useState(null);
  const [actionFilter, setActionFilter] = useState("all");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [detailId, setDetailId] = useState(null);
  const [selected, setSelected] = useState([]);
  const [modal, setModal] = useState(null); // {kind:'override', ids, rows} | {kind:'send'}
  const [busy, setBusy] = useState(false);
  const [modalError, setModalError] = useState(null);
  const gridRef = useRef(null);

  useEffect(() => {
    let live = true;
    setBatch(null);
    api.batch(batchId).then((b) => live && setBatch(b), (e) => live && setError(e));
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
      const needOverride = ids.filter(
        (id) => decision === "approve" && byId.get(id)?.action === "hold" && byId.get(id)?.decision !== "approve",
      );
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

  const rows = useMemo(() => {
    if (!batch) return [];
    return batch.rows.filter(
      (r) =>
        (actionFilter === "all" || r.action === actionFilter) &&
        (categoryFilter === "all" || (firstCategory(r) ?? "none") === categoryFilter),
    );
  }, [batch, actionFilter, categoryFilter]);

  const columnDefs = useMemo(
    () => [
      { field: "row_id", headerName: "Row", width: 90 },
      { field: "recipient_name", headerName: "Name", flex: 1, minWidth: 100 },
      { field: "receiver_email", headerName: "Email", flex: 1.2, minWidth: 120, tooltipField: "receiver_email" },
      {
        field: "amount",
        headerName: "Amount",
        width: 120,
        type: "rightAligned",
        comparator: (a, b) => Number(a) - Number(b),
        valueFormatter: (p) => `${p.value} ${p.data.currency}`,
      },
      { field: "action", headerName: "Model", width: 100, cellRenderer: (p) => <Badge value={p.value} /> },
      {
        headerName: "Top flag",
        width: 120,
        valueGetter: (p) => p.data.flags[0]?.type ?? "",
        valueFormatter: (p) => SHORT_FLAG[p.value] ?? p.value,
        tooltipValueGetter: (p) => FLAG_LABEL[p.value] ?? "",
      },
      {
        headerName: "Category",
        width: 110,
        valueGetter: (p) => firstCategory(p.data) ?? "",
        valueFormatter: (p) => (p.value === "new_record_matches_known_payee" ? "new record" : p.value === "two_records_same_payee" ? "both paid" : ""),
        tooltipValueGetter: (p) => CATEGORY_LABEL[p.value] ?? "",
      },
      { field: "decision", headerName: "Decision", width: 110, cellRenderer: (p) => <Badge value={p.value} kind="decision" /> },
      { headerName: "", width: 145, sortable: false, cellRenderer: DecisionButtons },
    ],
    [],
  );

  if (error && !batch) return <div className="alert error">{error.message}</div>;
  if (!batch) return <p className="muted">Loading batch…</p>;

  const s = batch.summary;
  const approved = batch.rows.filter((r) => r.decision === "approve");
  const flagged = batch.rows.filter((r) => r.action !== "proceed");
  const held = batch.rows.filter((r) => r.action === "hold");
  const detail = detailId ? byId.get(detailId) : null;
  const categories = [...new Set(batch.rows.map((r) => firstCategory(r) ?? "none"))];

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
        <button
          className="primary"
          disabled={locked || !approved.length}
          onClick={() => {
            setModalError(null);
            setModal({ kind: "send" });
          }}
        >
          Send approved ({approved.length})
        </button>
      </div>

      <div className="review-layout">
        <div className="grid-wrap">
          <AgGridReact
            ref={gridRef}
            rowData={rows}
            columnDefs={columnDefs}
            getRowId={(p) => p.data.row_id}
            context={{ decide, locked }}
            rowSelection={{ mode: "multiRow", enableClickSelection: false }}
            onSelectionChanged={(e) => setSelected(e.api.getSelectedRows().map((r) => r.row_id))}
            onRowClicked={(e) => setDetailId(e.data.row_id)}
            rowClassRules={{ "row-active": (p) => p.data.row_id === detailId }}
            overlayNoRowsTemplate="No rows match these filters."
          />
        </div>
        <aside className="detail card">
          {!detail && <p className="muted">Click a row to see why it was flagged.</p>}
          {detail && (
            <>
              <h3>
                {detail.recipient_name} <span className="muted small">{detail.row_id}</span>
              </h3>
              <p className="muted">
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
                <div className="small muted">{r.flags.map((f) => FLAG_LABEL[f.type] || f.type).join(", ")}</div>
                {r.flags[0] && <div className="small">{r.flags[0].explanation}</div>}
              </li>
            ))}
          </ul>
          {modalError && <div className="alert error">{modalError}</div>}
        </Modal>
      )}

      {modal?.kind === "send" && (
        <Modal
          title="Send approved payouts?"
          confirmLabel={`Send ${approved.length} payout${approved.length > 1 ? "s" : ""}`}
          onCancel={() => setModal(null)}
          onConfirm={confirmSend}
          busy={busy}
        >
          <p>
            <strong>{approved.length}</strong> approved rows, total <strong>{fmtTotals(approved)}</strong>, will be sent to the
            PayPal <strong>sandbox</strong>. Undecided and held rows are not sent. After this, decisions are locked.
          </p>
          {modalError && <div className="alert error">{modalError}</div>}
        </Modal>
      )}
    </section>
  );
}
