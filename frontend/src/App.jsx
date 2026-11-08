import { useEffect, useState } from "react";
import Upload from "./screens/Upload.jsx";
import Review from "./screens/Review.jsx";
import Payout from "./screens/Payout.jsx";
import Audit from "./screens/Audit.jsx";
import Eval from "./screens/Eval.jsx";

const TABS = [
  ["upload", "Upload"],
  ["review", "Review"],
  ["payout", "Payout"],
  ["audit", "Audit log"],
  ["eval", "Evaluation"],
];

// State lives in the URL hash (#tab=review&batch=b-...) so a reload keeps the screen.
function readHash() {
  const p = new URLSearchParams(window.location.hash.slice(1));
  return { tab: p.get("tab") || "upload", batch: p.get("batch") || null };
}

export default function App() {
  const [{ tab, batch }, setState] = useState(readHash);

  useEffect(() => {
    const onHash = () => setState(readHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const go = (nextTab, nextBatch = batch) => {
    const p = new URLSearchParams({ tab: nextTab });
    if (nextBatch) p.set("batch", nextBatch);
    window.location.hash = p.toString();
  };

  const needsBatch = ["review", "payout", "audit"].includes(tab);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          PayoutGuard <span className="muted">sandbox</span>
        </div>
        <nav>
          {TABS.map(([key, label]) => (
            <button
              key={key}
              className={`tab ${tab === key ? "active" : ""}`}
              disabled={["review", "payout", "audit"].includes(key) && !batch}
              onClick={() => go(key)}
            >
              {label}
            </button>
          ))}
        </nav>
        {batch && <div className="muted small">batch {batch}</div>}
      </header>
      <main>
        {tab === "upload" && <Upload onUploaded={(id) => go("review", id)} />}
        {needsBatch && !batch && <p className="empty">Upload a CSV first.</p>}
        {tab === "review" && batch && <Review batchId={batch} onSent={() => go("payout")} />}
        {tab === "payout" && batch && <Payout batchId={batch} />}
        {tab === "audit" && batch && <Audit batchId={batch} />}
        {tab === "eval" && <Eval />}
      </main>
      <footer className="muted small">
        Flags are advisory. Only rows an operator approved are sent, and the server enforces it.
      </footer>
    </div>
  );
}
