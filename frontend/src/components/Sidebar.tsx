import { NavLink } from "react-router-dom";
import { Icon, I } from "./icons";

interface SidebarProps {
  hostname: string;
  ip: string;
  firmware: string;
}

interface NavItem {
  to: string;
  label: string;
  iconPath: string;
}

const ITEMS: NavItem[] = [
  { to: "/home", label: "Home", iconPath: I.home },
  { to: "/camera", label: "Camera", iconPath: I.camera },
  { to: "/gallery", label: "Gallery", iconPath: I.gallery },
  { to: "/schedule", label: "Schedule", iconPath: I.schedule },
  { to: "/destinations", label: "Destinations", iconPath: I.upload },
  { to: "/terminal", label: "Terminal", iconPath: I.terminal },
  { to: "/settings/general", label: "Settings", iconPath: I.settings },
];

export function Sidebar({ hostname, ip, firmware }: SidebarProps) {
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
          <span>{item.label}</span>
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
