
import {
  LayoutDashboard,
  UploadCloud,
  ShieldCheck,
  Wallet,
  ClipboardList,
  ChartNoAxesCombined,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";

const NAVIGATION = [
  {
    heading: "WORKSPACE",
    items: [
      { id: "overview", label: "Overview", icon: LayoutDashboard },
      { id: "upload", label: "Upload batch", icon: UploadCloud },
      { id: "review", label: "Transaction review", icon: ShieldCheck },
      { id: "payout", label: "Payouts", icon: Wallet },
    ],
  },
  {
    heading: "MONITORING",
    items: [
      { id: "audit", label: "Audit log", icon: ClipboardList },
      { id: "eval", label: "Model evaluation", icon: ChartNoAxesCombined },
    ],
  },
];

export default function Sidebar({
  tab,
  go,
  batch,
  collapsed,
  setCollapsed,
}) {
  return (
    <aside className={`sidebar ${collapsed ? "collapsed" : ""}`}>
      <div className="sidebar-brand">
        <div className="brand-mark">
          <ShieldCheck size={23} />
        </div>

        {!collapsed && (
          <div className="brand-copy">
            <strong>PayoutGuard</strong>
            <span>PAYMENT INTELLIGENCE</span>
          </div>
        )}
      </div>

      <button
        className="sidebar-collapse"
        onClick={() => setCollapsed(!collapsed)}
        title="Toggle sidebar"
      >
        {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
      </button>

      <div className="sidebar-navigation">
        {NAVIGATION.map((group) => (
          <div className="nav-group" key={group.heading}>
            {!collapsed && (
              <p className="nav-heading">{group.heading}</p>
            )}

            {group.items.map(({ id, label, icon: Icon }) => {
              const disabled =
                ["review", "payout", "audit"].includes(id) && !batch;

              return (
                <button
                  key={id}
                  title={collapsed ? label : undefined}
                  className={`nav-item ${tab === id ? "selected" : ""}`}
                  disabled={disabled}
                  onClick={() => go(id)}
                >
                  <Icon size={19} strokeWidth={1.8} />
                  {!collapsed && <span>{label}</span>}

                  {id === "review" && batch && !collapsed && (
                    <span className="nav-indicator" />
                  )}
                </button>
              );
            })}
          </div>
        ))}
      </div>

      <div className="sidebar-bottom">
        <div className="sidebar-status">
          <span className="status-dot" />
          {!collapsed && <span>Sandbox environment</span>}
        </div>

        <div className="sidebar-version">
          {!collapsed && <span>PAYOUTGUARD · v0.1</span>}
        </div>
      </div>
    </aside>
  );
}
