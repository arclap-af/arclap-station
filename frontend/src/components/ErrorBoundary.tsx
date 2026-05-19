import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";

interface State {
  error: Error | null;
}

interface Props {
  children: ReactNode;
}

export class ErrorBoundary extends Component<Props, State> {
  override state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    // Surface to the console — production logs ship via the backend.
    console.error("[arclap] uncaught", error, info);
  }

  override render(): ReactNode {
    if (this.state.error) {
      return (
        <div className="as-login">
          <div className="as-login-card" style={{ maxWidth: 520 }}>
            <div style={{ fontSize: 11, color: "var(--as-ink-3)", textTransform: "uppercase", letterSpacing: 0.08, fontWeight: 700 }}>
              Arclap Station
            </div>
            <h2 style={{ margin: "8px 0 6px", fontSize: 22, fontWeight: 700 }}>Something went wrong</h2>
            <div style={{ fontSize: 13, color: "var(--as-ink-3)", marginBottom: 14 }}>{this.state.error.message}</div>
            <button className="as-btn as-btn-primary" onClick={() => this.setState({ error: null })}>
              Reload view
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
