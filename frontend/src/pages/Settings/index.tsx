import { useNavigate, useParams } from "react-router-dom";

import { Activity } from "./tabs/Activity";
import { Diagnostics } from "./tabs/Diagnostics";
import { General } from "./tabs/General";
import { Health } from "./tabs/Health";
import { Network } from "./tabs/Network";
import { Security } from "./tabs/Security";
import { Storage } from "./tabs/Storage";
import { System } from "./tabs/System";
import { Logs } from "./tabs/Logs";

const TABS = [
  { id: "general", label: "General", Component: General },
  // Health = the live self-test + alert config. Placed early — it's
  // the "is this station OK and who gets told if not" surface.
  { id: "health", label: "Health", Component: Health },
  { id: "network", label: "Network", Component: Network },
  { id: "security", label: "Security", Component: Security },
  { id: "storage", label: "Storage", Component: Storage },
  { id: "system", label: "System", Component: System },
  { id: "diagnostics", label: "Diagnostics", Component: Diagnostics },
  // Activity = audit-log timeline ("what happened on this station").
  // Distinct from Logs (which is raw journald — system / debug noise).
  { id: "activity", label: "Activity", Component: Activity },
  { id: "logs", label: "Logs", Component: Logs },
] as const;

export function Settings() {
  const navigate = useNavigate();
  const { tab = "general" } = useParams<{ tab: string }>();
  const Current = TABS.find((t) => t.id === tab)?.Component ?? General;

  return (
    <div className="as-scroll">
      <div className="as-page" style={{ maxWidth: 1100 }}>
        <h1 className="as-h1">Settings</h1>
        <div className="as-h1-sub">Station configuration · network · security · system</div>
        <div className="as-tabs" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              className={`as-tab ${tab === t.id ? "active" : ""}`}
              onClick={() => navigate(`/settings/${t.id}`)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <Current />
      </div>
    </div>
  );
}
