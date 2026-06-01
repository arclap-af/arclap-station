import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";

import { Button } from "../../components/Button";
import { Icon, I } from "../../components/icons";
import { setup } from "../../lib/bridge/setup";

import { Welcome } from "./steps/Welcome";
import { PIN } from "./steps/PIN";
import { CameraStep } from "./steps/Camera";
import { Station } from "./steps/Station";
import { Network } from "./steps/Network";
import { Destination } from "./steps/Destination";
import { Schedule } from "./steps/Schedule";
import { Pair } from "./steps/Pair";
import { Check } from "./steps/Check";
import { Done } from "./steps/Done";

export const SETUP_STEPS = [
  { id: "welcome", title: "Welcome", sub: "Let's set up your station" },
  { id: "pin", title: "Set a PIN", sub: "Protect this Pi" },
  { id: "camera", title: "Connect camera", sub: "Plug in the DSLR" },
  { id: "station", title: "Name station", sub: "Identify this device" },
  { id: "network", title: "Network", sub: "Check connectivity" },
  { id: "dest", title: "Where photos go", sub: "Pick a destination" },
  { id: "schedule", title: "Capture schedule", sub: "How often to shoot" },
  { id: "pair", title: "Pair to cloud", sub: "Optional · enables remote" },
  { id: "check", title: "Acceptance check", sub: "Run the full test" },
  { id: "done", title: "All ready", sub: "Station is live" },
] as const;

export type SetupStepId = (typeof SETUP_STEPS)[number]["id"];

export interface SetupState {
  pin: string[];
  pinError: string | null;
  cameraDetected: boolean;
  cameraModel: string | null;
  cameraLens: string | null;
  cameraFirmware: string | null;
  cameraBattery: number | null;
  cameraShutterCount: number | null;
  stationName: string;
  timezone: string;
  destType: "s3" | "sftp" | "ftp" | "webhook" | "local" | "mqtt" | "arc";
  destName: string;
  destConfig: Record<string, unknown>;
  schedInterval: number;
  schedFrom: string;
  schedTo: string;
  schedDays: string[];
  pair: boolean;
  pairCode: string;
  acceptancePassed: boolean;
}

const INITIAL: SetupState = {
  pin: ["", "", "", "", "", ""],
  pinError: null,
  cameraDetected: false,
  cameraModel: null,
  cameraLens: null,
  cameraFirmware: null,
  cameraBattery: null,
  cameraShutterCount: null,
  stationName: "Arclap Station",
  timezone: "Europe/Zurich",
  destType: "s3",
  destName: "Primary archive",
  destConfig: { endpoint: "", bucket: "", prefix: "/photos/{yyyy}/{mm}/{dd}/" },
  schedInterval: 15,
  schedFrom: "06:00",
  schedTo: "19:00",
  schedDays: ["mon", "tue", "wed", "thu", "fri", "sat"],
  pair: false,
  pairCode: "",
  acceptancePassed: false,
};

export function SetupWizard() {
  const navigate = useNavigate();
  const [stepIndex, setStepIndex] = useState(0);
  const [state, setState] = useState<SetupState>(INITIAL);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const cur = SETUP_STEPS[stepIndex];
  const total = SETUP_STEPS.length;

  const update = <K extends keyof SetupState>(k: K, v: SetupState[K]) =>
    setState((s) => ({ ...s, [k]: v }));

  const { data: status } = useQuery({ queryKey: ["setup.status"], queryFn: setup.status });

  useEffect(() => {
    if (status && !status.first_boot) {
      navigate("/login", { replace: true });
    }
  }, [status, navigate]);

  const submit = useMutation({
    mutationFn: async () => {
      setSubmitError(null);
      switch (cur.id) {
        case "pin":
          await setup.setPin(state.pin.join(""));
          break;
        case "station":
          await setup.station(state.stationName, state.timezone);
          break;
        case "dest":
          // Setup wizard only TESTS the destination here; the user can
          // create the real destination from the Destinations page later.
          await setup.destination({
            type: state.destType,
            config: state.destConfig,
          });
          break;
        case "schedule":
          await setup.schedule({
            interval_min: state.schedInterval,
            from_time: state.schedFrom,
            to_time: state.schedTo,
            days: state.schedDays,
            name: state.destName || "Default",
          });
          break;
        case "pair":
          // Only call /setup/pair when pairing is enabled — backend
          // requires pair_code min_length=4.
          if (state.pair && state.pairCode && state.pairCode.length >= 4) {
            await setup.pair(state.pairCode);
          }
          break;
        case "done":
          await setup.finish();
          break;
        default:
          return;
      }
    },
    onSuccess: () => {
      if (cur.id === "done") {
        navigate("/home", { replace: true });
      } else {
        setStepIndex((i) => Math.min(total - 1, i + 1));
      }
    },
    onError: (err) => {
      setSubmitError(err instanceof Error ? err.message : String(err));
    },
  });

  const canAdvance = useMemo(() => {
    switch (cur.id) {
      case "pin":
        return state.pin.every((d) => /^\d$/.test(d));
      case "station":
        return state.stationName.trim().length > 0;
      // Camera detection + acceptance check are advisory, not gating —
      // a station can be commissioned without a DSLR plugged in (offline
      // mode for testing), and the acceptance check is a sanity probe
      // not a hard requirement to finish setup.
      default:
        return true;
    }
  }, [cur.id, state]);

  const next = () => {
    if (!canAdvance) return;
    if (cur.id === "welcome" || cur.id === "network" || cur.id === "camera" || cur.id === "check") {
      setStepIndex((i) => Math.min(total - 1, i + 1));
      return;
    }
    submit.mutate();
  };

  const back = () => {
    setSubmitError(null);
    setStepIndex((i) => Math.max(0, i - 1));
  };

  return (
    <div className="as-setup-shell">
      <div style={{ padding: "18px 32px", borderBottom: "1px solid var(--as-line)", background: "rgba(10,14,20,0.7)", backdropFilter: "blur(6px)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 14 }}>
          <div className="as-mark" style={{ width: 32, height: 32, fontSize: 15 }}>A</div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 11, color: "var(--as-ink-3)", textTransform: "uppercase", letterSpacing: 0.08, fontWeight: 600 }}>
              Arclap Station · first-time setup
            </div>
            <div style={{ fontSize: 14, fontWeight: 600, marginTop: 2 }}>
              Step {stepIndex + 1} of {total} — {cur.title}
            </div>
          </div>
        </div>
        <div style={{ display: "flex", gap: 4 }}>
          {SETUP_STEPS.map((s, i) => (
            <div
              key={s.id}
              className={`as-setup-seg${i < stepIndex ? " done" : i === stepIndex ? " current" : ""}`}
            />
          ))}
        </div>
      </div>

      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", padding: "32px 24px" }}>
        <div key={cur.id} className="as-setup-step" style={{ width: "100%", maxWidth: 560 }}>
          <div style={{ textAlign: "center", marginBottom: 28 }}>
            <h1 style={{ fontSize: 32, fontWeight: 700, letterSpacing: "-0.02em", margin: "0 0 8px" }}>{cur.title}</h1>
            <div style={{ fontSize: 14, color: "var(--as-ink-3)" }}>{cur.sub}</div>
          </div>

          <div className="as-card" style={{ padding: 28 }}>
            {cur.id === "welcome" && <Welcome total={total} />}
            {cur.id === "pin" && <PIN state={state} update={update} />}
            {cur.id === "camera" && <CameraStep state={state} update={update} />}
            {cur.id === "station" && <Station state={state} update={update} />}
            {cur.id === "network" && <Network />}
            {cur.id === "dest" && <Destination state={state} update={update} />}
            {cur.id === "schedule" && <Schedule state={state} update={update} />}
            {cur.id === "pair" && <Pair state={state} update={update} />}
            {cur.id === "check" && <Check onResult={(ok) => update("acceptancePassed", ok)} />}
            {cur.id === "done" && <Done state={state} />}
          </div>

          {submitError && (
            <div className="as-banner bad" role="alert" style={{ marginTop: 14, marginBottom: 0 }}>
              {submitError}
            </div>
          )}

          <div style={{ display: "flex", justifyContent: "space-between", marginTop: 20, gap: 8 }}>
            <Button onClick={back} disabled={stepIndex === 0 || submit.isPending}>
              ← Back
            </Button>
            <div style={{ display: "flex", gap: 8 }}>
              {cur.id !== "welcome" && cur.id !== "done" && cur.id !== "pin" && (
                <Button
                  onClick={() => setStepIndex((i) => Math.min(total - 1, i + 1))}
                  disabled={submit.isPending}
                >
                  Skip
                </Button>
              )}
              <Button variant="primary" onClick={next} disabled={!canAdvance || submit.isPending}>
                {cur.id === "welcome" && "Get started"}
                {cur.id !== "welcome" && cur.id !== "done" && (submit.isPending ? "Working…" : "Continue")}
                {cur.id === "done" && (submit.isPending ? "Finishing…" : "Open station")}
                {!submit.isPending && <Icon d={cur.id === "done" ? I.check : I.arrowR} size={14} />}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
