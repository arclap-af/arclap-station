import { Icon, I } from "../../../components/icons";

interface Props {
  total: number;
}

export function Welcome({ total }: Props) {
  return (
    <div style={{ textAlign: "center" }}>
      <div
        style={{
          width: 80,
          height: 80,
          borderRadius: 20,
          background: "var(--as-accent)",
          color: "#04140e",
          margin: "0 auto 20px",
          display: "grid",
          placeItems: "center",
        }}
      >
        <Icon d={I.zap} size={40} stroke={2} />
      </div>
      <div style={{ fontSize: 15, lineHeight: 1.6, color: "var(--as-ink-2)", marginBottom: 20 }}>
        This wizard sets up everything in one go: PIN, camera, network, photo destination, schedule, and a full
        pre-ship acceptance check.
      </div>
      <div
        style={{
          display: "flex",
          justifyContent: "center",
          gap: 24,
          fontSize: 12.5,
          color: "var(--as-ink-3)",
          padding: "14px 0",
          borderTop: "1px solid var(--as-line)",
          borderBottom: "1px solid var(--as-line)",
        }}
      >
        <span>
          <strong style={{ color: "var(--as-accent-2)" }}>~5</strong> minutes
        </span>
        <span>
          <strong style={{ color: "var(--as-accent-2)" }}>{total - 1}</strong> steps
        </span>
        <span>
          <strong style={{ color: "var(--as-accent-2)" }}>40</strong> checks
        </span>
      </div>
    </div>
  );
}
