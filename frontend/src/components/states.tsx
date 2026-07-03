import type { ReactNode } from "react";

import { Icon, I } from "./icons";

/**
 * Shared async-state blocks so every page renders loading / error the
 * same way instead of each rolling its own (or, worse, showing a blank
 * panel when a fetch fails on a flaky Pi LAN).
 *
 * Use <Async> to wrap a Tanstack Query result, or the individual
 * <LoadingState> / <ErrorState> where finer control is needed.
 */

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="as-async">
      <div className="as-spinner" aria-hidden />
      <div className="as-async-msg">{label}</div>
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
  label = "Couldn't load this",
}: {
  error?: unknown;
  onRetry?: () => void;
  label?: string;
}) {
  const detail = error instanceof Error ? error.message : error ? String(error) : null;
  return (
    <div className="as-async">
      <div className="as-async-icon bad">
        <Icon d={I.zap} size={22} />
      </div>
      <div className="as-async-title">{label}</div>
      {detail && <div className="as-async-msg mono">{detail}</div>}
      {onRetry && (
        <button className="as-btn as-btn-primary" style={{ marginTop: 12 }} onClick={onRetry}>
          <Icon d={I.refresh} size={13} /> Retry
        </button>
      )}
    </div>
  );
}

interface AsyncProps {
  isLoading: boolean;
  isError: boolean;
  error?: unknown;
  onRetry?: () => void;
  loadingLabel?: string;
  errorLabel?: string;
  children: ReactNode;
}

/**
 * Render `children` only once a query has resolved; otherwise show a
 * consistent loading or error+retry block. Keeps the state-handling
 * boilerplate out of every page.
 */
export function Async({
  isLoading,
  isError,
  error,
  onRetry,
  loadingLabel,
  errorLabel,
  children,
}: AsyncProps) {
  if (isError) return <ErrorState error={error} onRetry={onRetry} label={errorLabel} />;
  if (isLoading) return <LoadingState label={loadingLabel} />;
  return <>{children}</>;
}
