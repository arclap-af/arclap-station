import { useEffect } from "react";
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { Sidebar } from "./components/Sidebar";
import { Topbar } from "./components/Topbar";
import { URLBar } from "./components/URLBar";
import { auth } from "./lib/bridge/auth";
import { setup } from "./lib/bridge/setup";
import { home as homeApi } from "./lib/bridge/home";

import { Login } from "./pages/Login";
import { Home } from "./pages/Home";
import { SetupWizard } from "./pages/Setup";
import { CameraPage } from "./pages/Camera";
import { CameraProperties } from "./pages/Camera/Properties";
import { Gallery } from "./pages/Gallery";
import { SchedulePage } from "./pages/Schedule";
import { Destinations } from "./pages/Destinations";
import { Terminal } from "./pages/Terminal";
import { Settings } from "./pages/Settings";

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<RootGate />} />
        <Route path="/setup/*" element={<SetupWizard />} />
        <Route path="/login" element={<Login />} />
        <Route
          path="/*"
          element={
            <RequireAuth>
              <Shell />
            </RequireAuth>
          }
        />
      </Routes>
    </BrowserRouter>
  );
}

function RootGate() {
  // Initial routing. Talks to /setup/status and /auth/session to decide where to send the user.
  const { data: status, isLoading: setupLoading } = useQuery({
    queryKey: ["setup.status"],
    queryFn: setup.status,
  });
  const { data: session, isLoading: sessionLoading } = useQuery({
    queryKey: ["auth.session"],
    queryFn: auth.session,
    enabled: !!status && !status.first_boot,
  });

  if (setupLoading) return <LoadingScreen label="Reading station state…" />;
  if (status?.first_boot) return <Navigate to="/setup" replace />;
  if (sessionLoading) return <LoadingScreen label="Checking session…" />;
  return <Navigate to={session?.authenticated ? "/home" : "/login"} replace />;
}

function RequireAuth({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate();
  const location = useLocation();
  const { data: session, isLoading } = useQuery({
    queryKey: ["auth.session"],
    queryFn: auth.session,
  });
  useEffect(() => {
    if (!isLoading && !session?.authenticated) {
      navigate(`/login?next=${encodeURIComponent(location.pathname)}`, { replace: true });
    }
  }, [isLoading, session, navigate, location.pathname]);
  if (isLoading) return <LoadingScreen label="Checking session…" />;
  if (!session?.authenticated) return null;
  return <>{children}</>;
}

function Shell() {
  const { data: telemetry } = useQuery({
    queryKey: ["home.telemetry"],
    queryFn: homeApi.telemetry,
    refetchInterval: 15_000,
  });
  const hostname = telemetry?.hostname ?? "arclap-station";
  const ip = telemetry?.ip ?? "—";
  const firmware = telemetry?.firmware ?? "0.1.0";
  const status = telemetry?.status ?? "online";
  return (
    <>
      <URLBar hostname={hostname} ip={ip} status={status} />
      <div className="as-shell">
        <Sidebar hostname={hostname} ip={ip} firmware={firmware} />
        <div className="as-main">
          <Topbar
            right={
              <div style={{ marginLeft: "auto", fontSize: 12, color: "var(--as-ink-3)", display: "flex", gap: 14 }}>
                <span>Uptime {fmtUptime(telemetry?.uptime_seconds ?? 0)}</span>
              </div>
            }
          />
          <Routes>
            <Route index element={<Navigate to="/home" replace />} />
            <Route path="/home" element={<Home />} />
            <Route path="/camera" element={<CameraPage />} />
            <Route path="/camera/properties" element={<CameraProperties />} />
            <Route path="/gallery" element={<Gallery />} />
            <Route path="/schedule" element={<SchedulePage />} />
            <Route path="/destinations" element={<Destinations />} />
            <Route path="/terminal" element={<Terminal />} />
            <Route path="/settings" element={<Navigate to="/settings/general" replace />} />
            <Route path="/settings/:tab" element={<Settings />} />
            <Route path="*" element={<Navigate to="/home" replace />} />
          </Routes>
        </div>
      </div>
    </>
  );
}

function LoadingScreen({ label }: { label: string }) {
  return (
    <div className="as-login">
      <div className="as-login-card" style={{ textAlign: "center" }}>
        <div className="as-mark" style={{ margin: "0 auto 18px" }}>
          A
        </div>
        <div style={{ fontSize: 13, color: "var(--as-ink-3)" }}>{label}</div>
      </div>
    </div>
  );
}

function fmtUptime(seconds: number): string {
  if (!seconds) return "—";
  const d = Math.floor(seconds / 86_400);
  const h = Math.floor((seconds % 86_400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}
