import { Icon, I } from "./icons";

/**
 * Sticky banner shown when the cockpit can't reach the station API.
 *
 * The Shell already polls /home telemetry every 15s; when that query
 * flips to an error state (LAN drop, service restart, Pi reboot) we
 * surface it here instead of letting every panel quietly go stale.
 * Tanstack keeps retrying in the background, so the banner clears itself
 * on the next successful poll.
 */
export function OfflineBanner({ show, onRetry }: { show: boolean; onRetry?: () => void }) {
  if (!show) return null;
  return (
    <div className="as-offline-banner" role="status" aria-live="polite">
      <Icon d={I.zap} size={14} />
      <span>Connection to the station lost — retrying…</span>
      {onRetry && (
        <button className="as-offline-retry" onClick={onRetry}>
          Retry now
        </button>
      )}
    </div>
  );
}
