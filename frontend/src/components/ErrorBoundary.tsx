import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";

interface State {
  error: Error | null;
}

interface Props {
  children: ReactNode;
  /**
   * Inline variant renders a compact card in the content area instead of
   * the full-screen fallback, so the sidebar + topbar stay put when a
   * single route crashes. Used per-route inside the authenticated shell.
   */
  inline?: boolean;
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

  private reset = () => this.setState({ error: null });

  override render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;

    if (this.props.inline) {
      return (
        <div className="as-scroll">
          <div className="as-page" style={{ maxWidth: 620 }}>
            <div className="as-card" style={{ padding: 22 }}>
              <div style={{ fontSize: 11, color: "var(--as-bad)", textTransform: "uppercase", letterSpacing: 0.08, fontWeight: 700 }}>
                Page error
              </div>
              <h2 style={{ margin: "8px 0 6px", fontSize: 20, fontWeight: 700 }}>This view hit a problem</h2>
              <div style={{ fontSize: 13, color: "var(--as-ink-3)", marginBottom: 16, wordBreak: "break-word" }}>
                {error.message}
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <button className="as-btn as-btn-primary" onClick={this.reset}>
                  Retry
                </button>
                <button className="as-btn" onClick={() => window.location.reload()}>
                  Reload cockpit
                </button>
              </div>
            </div>
          </div>
        </div>
      );
    }

    return (
      <div className="as-login">
        <div className="as-login-card" style={{ maxWidth: 520 }}>
          <div style={{ fontSize: 11, color: "var(--as-ink-3)", textTransform: "uppercase", letterSpacing: 0.08, fontWeight: 700 }}>
            Arclap Station
          </div>
          <h2 style={{ margin: "8px 0 6px", fontSize: 22, fontWeight: 700 }}>Something went wrong</h2>
          <div style={{ fontSize: 13, color: "var(--as-ink-3)", marginBottom: 14 }}>{error.message}</div>
          <button className="as-btn as-btn-primary" onClick={this.reset}>
            Reload view
          </button>
        </div>
      </div>
    );
  }
}
