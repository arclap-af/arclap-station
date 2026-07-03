import { NavLink } from "react-router-dom";
import { Icon, I } from "./icons";
import { useI18n } from "../lib/i18n";

interface SidebarProps {
  hostname: string;
  ip: string;
  firmware: string;
}

interface NavItem {
  to: string;
  labelKey: string;
  iconPath: string;
}

const ITEMS: NavItem[] = [
  { to: "/home", labelKey: "nav.home", iconPath: I.home },
  { to: "/camera", labelKey: "nav.camera", iconPath: I.camera },
  { to: "/gallery", labelKey: "nav.gallery", iconPath: I.gallery },
  { to: "/schedule", labelKey: "nav.schedule", iconPath: I.schedule },
  { to: "/destinations", labelKey: "nav.destinations", iconPath: I.upload },
  { to: "/terminal", labelKey: "nav.terminal", iconPath: I.terminal },
  { to: "/settings/general", labelKey: "nav.settings", iconPath: I.settings },
];

export function Sidebar({ hostname, ip, firmware }: SidebarProps) {
  const { t } = useI18n();
  return (
    <aside className="as-side">
      <div className="as-brand">
        <div className="as-mark">A</div>
        <div>
          <div className="as-brand-title">Arclap</div>
          <div className="as-brand-sub">{hostname}</div>
        </div>
      </div>
      {ITEMS.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          className={({ isActive }) =>
            `as-nav-item ${isActive || (item.to === "/settings/general" && window.location.pathname.startsWith("/settings")) ? "active" : ""}`
          }
        >
          <Icon d={item.iconPath} size={18} />
          <span>{t(item.labelKey)}</span>
        </NavLink>
      ))}
      <div className="as-side-foot">
        <div className="as-pi-info">
          {ip}
          <br />v{firmware}
        </div>
      </div>
    </aside>
  );
}
