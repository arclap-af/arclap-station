import { useMutation, useQuery } from "@tanstack/react-query";

import { Button } from "../../../components/Button";
import { Pill } from "../../../components/Pill";
import { settings } from "../../../lib/bridge/settings";

export function System() {
  const { data } = useQuery({ queryKey: ["settings.system"], queryFn: settings.system });
  const restart = useMutation({ mutationFn: settings.restart });
  const reboot = useMutation({ mutationFn: settings.reboot });
  const factory = useMutation({ mutationFn: settings.factoryReset });

  if (!data) return <div style={{ color: "var(--as-ink-3)" }}>Loading…</div>;

  return (
    <div className="as-grid-2" style={{ alignItems: "start" }}>
      <div className="as-card">
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 14 }}>Hardware</div>
        <Row label="Model" val={data.hardware.model} />
        <Row label="Serial" val={data.hardware.serial} mono />
        <Row label="CPU" val={`${data.hardware.cpu_pct.toFixed(0)}% · ${data.hardware.cpu_temp_c.toFixed(1)}°C`} mono />
        <Row label="Memory" val={`${data.hardware.memory_used_mb} / ${data.hardware.memory_total_mb} MB`} mono />
        <Row label="UPS" val={data.hardware.ups} />
        <Row label="Watchdog" val={data.hardware.watchdog} />
      </div>
      <div className="as-card">
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 14 }}>Firmware</div>
        <Row label="Current" val={data.firmware.current} mono />
        <Row label="Channel" val={data.firmware.channel} />
        <Row label="Last check" val={data.firmware.last_check} />
        <Row label="Available" val={data.firmware.available} />
      </div>
      <div className="as-card">
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 14 }}>Cloud pairing</div>
        <Row
          label="Status"
          val=""
          customVal={<Pill tone={data.cloud.paired ? "ok" : "gray"}>{data.cloud.paired ? "Paired" : "Standalone"}</Pill>}
        />
        <Row label="MQTT broker" val={data.cloud.broker ?? "—"} mono />
        <Row label="Cockpit URL" val={data.cloud.cockpit_url ?? "—"} mono />
      </div>
      <div className="as-card">
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 14, color: "var(--as-bad)" }}>Danger zone</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <Button onClick={() => restart.mutate("arclap-station")}>Restart capture service</Button>
          <Button onClick={() => reboot.mutate()}>Reboot Pi</Button>
          <Button
            style={{ color: "var(--as-bad)" }}
            onClick={() => {
              if (window.confirm("Factory reset wipes all configuration. Continue?")) factory.mutate();
            }}
          >
            Factory reset
          </Button>
        </div>
      </div>
    </div>
  );
}

function Row({ label, val, mono, customVal }: { label: string; val: string; mono?: boolean; customVal?: React.ReactNode }) {
  return (
    <div className="as-stat-row">
      <span className="as-stat-label">{label}</span>
      <span className={`as-stat-val${mono ? " mono" : ""}`}>{customVal ?? val}</span>
    </div>
  );
}
