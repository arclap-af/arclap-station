import { Suspense, lazy, useEffect } from "react";
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { ErrorBoundary } from "./components/ErrorBoundary";
import { LoadingState } from "./components/states";
import { OfflineBanner } from "./components/OfflineBanner";
import { Sidebar } from "./components/Sidebar";
import { ToastProvider } from "./components/ToastQueue";
import { Topbar } from "./components/Topbar";
import { URLBar } from "./components/URLBar";
import { auth } from "./lib/bridge/auth";
import { useGlobalHotkeys } from "./lib/hotkeys";
import { setup } from "./lib/bridge/setup";
import { home as homeApi } from "./lib/bridge/home";

import { Login } from "./pages/Login";

// Route components are code-split so the initial bundle stays lean — most
// notably the Terminal page pulls in xterm (~290 KB), which no longer
// ships on first paint for operators who never open a shell. Named
// exports are unwrapped to the default shape React.lazy expects.
const Home = lazy(() => import("./pages/Home").then((m) => ({ default: m.Home })));
const SetupWizard = lazy(() => import("./pages/Setup").then((m) => ({ default: m.SetupWizard })));
const CameraPage = lazy(() => import("./pages/Camera").then((m) => ({ default: m.CameraPage })));
const CameraProperties = lazy(() =>
  import("./pages/Camera/Properties").then((m) => ({ default: m.CameraProperties })),
);
const Gallery = lazy(() => import("./pages/Gallery").then((m) => ({ default: m.Gallery })));
const SchedulePage = lazy(() => import("./pages/Schedule").then((m) => ({ default: m.SchedulePage })));
const Destinations = lazy(() => import("./pages/Destinations").then((m) => ({ default: m.Destinations })));
const Terminal = lazy(() => import("./pages/Terminal").then((m) => ({ default: m.Terminal })));
const Settings = lazy(() => import("./pages/Settings").then((m) => ({ default: m.Settings })));

export function App() {
  return (
    <BrowserRouter>
      <ToastProvider>
        <Suspense fallback={<LoadingScreen label="Loading…" />}>
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
        </Suspense>
      </ToastProvider>
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
  return <Navigate to={session?.logged_in ? "/home" : "/login"} replace />;
}

function RequireAuth({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate();
  const location = useLocation();
  const { data: session, isLoading } = useQuery({
    queryKey: ["auth.session"],
    queryFn: auth.session,
    // Re-verify periodically + on tab focus so an expired 12h session
    // redirects to /login instead of leaving a zombie cockpit that shows
    // stale data and silently 401s every action.
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  });
  useEffect(() => {
    if (!isLoading && !session?.logged_in) {
      navigate(`/login?next=${encodeURIComponent(location.pathname)}`, { replace: true });
    }
  }, [isLoading, session, navigate, location.pathname]);
  if (isLoading) return <LoadingScreen label="Checking session…" />;
  if (!session?.logged_in) return null;
  return <>{children}</>;
}

function Shell() {
  // Wire global keyboard shortcuts inside the authenticated shell only —
  // so Login + Setup pages don't intercept keys.
  useGlobalHotkeys();
  const location = useLocation();
  const {
    data: telemetry,
    isError: telemetryDown,
    refetch: refetchTelemetry,
  } = useQuery({
    queryKey: ["home.telemetry"],
    queryFn: homeApi.telemetry,
    refetchInterval: 15_000,
  });
  const hostname = telemetry?.hostname ?? "arclap-station";
  const ip = telemetry?.ip ?? "—";
  const firmware = telemetry?.firmware ?? "—";
  const status = telemetry?.status ?? "online";
  return (
    <>
      <URLBar hostname={hostname} ip={ip} status={status} />
      <OfflineBanner show={telemetryDown} onRetry={() => refetchTelemetry()} />
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
          {/* Per-route boundary: a crash in one page shows an inline retry
              card while the sidebar/topbar stay usable. Keyed on the path
              so navigating away clears a stuck error. Suspense drives the
              code-split page chunks. */}
          <ErrorBoundary inline key={location.pathname}>
            <Suspense fallback={<LoadingState label="Loading view…" />}>
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
            </Suspense>
          </ErrorBoundary>
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
