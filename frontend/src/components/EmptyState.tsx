import type { ReactNode } from "react";

import { Icon } from "./icons";

interface EmptyStateProps {
  /** Icon path from the `I` set in icons.tsx. */
  icon: string;
  /** Bold one-line headline. */
  title: string;
  /** Supporting sentence(s) explaining the empty state / what to do next. */
  message?: ReactNode;
  /** Optional call-to-action button(s) rendered below the message. */
  action?: ReactNode;
}

/**
 * Shared empty-state block. Used wherever a list is genuinely empty
 * (Gallery with no photos, Schedule with no schedules, Activity with
 * no events, Destinations with none configured). Centralised so every
 * empty surface reads the same — a circular icon chip, a headline, a
 * supporting line, and an optional CTA — instead of each page rolling
 * its own ad-hoc "Nothing here" text.
 */
export function EmptyState({ icon, title, message, action }: EmptyStateProps) {
  return (
    <div className="as-empty">
      <div className="as-empty-icon">
        <Icon d={icon} size={24} />
      </div>
      <div className="as-empty-title">{title}</div>
      {message && <div className="as-empty-msg">{message}</div>}
      {action && <div className="as-empty-cta">{action}</div>}
    </div>
  );
}
