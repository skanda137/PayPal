
import { useEffect, useState } from "react";

import Sidebar from "./components/Sidebar.jsx";
import Upload from "./screens/Upload.jsx";
import Review from "./screens/Review.jsx";
import Payout from "./screens/Payout.jsx";
import Audit from "./screens/Audit.jsx";
import Eval from "./screens/Eval.jsx";

const PAGE_INFO = {
  overview: {
    title: "Overview",
    subtitle: "Monitor your payment operations and risk activity.",
  },
  upload: {
    title: "Upload batch",
    subtitle: "Import a payout batch and prepare it for review.",
  },
  review: {
    title: "Transaction review",
    subtitle: "Inspect flagged transactions and make operator decisions.",
  },
  payout: {
    title: "Payouts",
    subtitle: "Review and manage approved payout transactions.",
  },
  audit: {
    title: "Audit log",
    subtitle: "Track operator decisions and payout activity.",
  },
  eval: {
    title: "Model evaluation",
    subtitle: "Evaluate model performance and risk detection.",
  },
};

// State lives in the URL hash so a reload keeps the selected screen.
function readHash() {
  const p = new URLSearchParams(window.location.hash.slice(1));

  return {
    tab: p.get("tab") || "overview",
    batch: p.get("batch") || null,
  };
}

export default function App() {
  const [{ tab, batch }, setState] = useState(readHash);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    const onHash = () => setState(readHash());

    window.addEventListener("hashchange", onHash);

    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const go = (nextTab, nextBatch = batch) => {
    const p = new URLSearchParams({ tab: nextTab });

    if (nextBatch) {
      p.set("batch", nextBatch);
    }

    window.location.hash = p.toString();
  };

  const needsBatch = ["review", "payout", "audit"].includes(tab);
  const page = PAGE_INFO[tab] || PAGE_INFO.overview;

  return (
    <div className={`app-shell ${collapsed ? "sidebar-collapsed" : ""}`}>
      <Sidebar
        tab={tab}
        go={go}
        batch={batch}
        collapsed={collapsed}
        setCollapsed={setCollapsed}
      />

      <div className="app-workspace">
        <header className="page-header">
          <div>
            <h1>{page.title}</h1>
            <p>{page.subtitle}</p>
          </div>

          <div className="header-right">
            <span className="environment-badge">
              <span className="status-dot" />
              Sandbox environment
            </span>

            {batch && (
              <span className="batch-label">
                Batch: {batch}
              </span>
            )}
          </div>
        </header>

        <main className="page-content">
          {tab === "overview" && (
            <section className="overview-welcome">
              <div className="welcome-card">
                <span className="welcome-eyebrow">
                  PAYMENT INTELLIGENCE
                </span>

                <h2>Welcome to PayoutGuard</h2>

                <p>
                  Your workspace for reviewing payout transactions,
                  identifying potential risks, and maintaining a clear
                  audit trail.
                </p>

                <button
                  className="primary-button"
                  onClick={() => go("upload")}
                >
                  Start with a batch
                </button>
              </div>

              <div className="overview-placeholder-grid">
                <div className="overview-placeholder">
                  <span className="placeholder-icon">01</span>
                  <h3>Upload</h3>
                  <p>Import your payout CSV file.</p>
                </div>

                <div className="overview-placeholder">
                  <span className="placeholder-icon">02</span>
                  <h3>Review</h3>
                  <p>Inspect risk flags and approve transactions.</p>
                </div>

                <div className="overview-placeholder">
                  <span className="placeholder-icon">03</span>
                  <h3>Pay out</h3>
                  <p>Send approved transactions to the sandbox.</p>
                </div>
              </div>
            </section>
          )}

          {tab === "upload" && (
            <Upload onUploaded={(id) => go("review", id)} />
          )}

          {needsBatch && !batch && (
            <p className="empty">Upload a CSV first.</p>
          )}

          {tab === "review" && batch && (
            <Review
              batchId={batch}
              onSent={() => go("payout")}
            />
          )}

          {tab === "payout" && batch && (
            <Payout batchId={batch} />
          )}

          {tab === "audit" && batch && (
            <Audit batchId={batch} />
          )}

          {tab === "eval" && <Eval />}
        </main>

        <footer className="workspace-footer">
          Flags are advisory. Only rows an operator approved are sent,
          and the server enforces it.
        </footer>
      </div>
    </div>
  );
}
