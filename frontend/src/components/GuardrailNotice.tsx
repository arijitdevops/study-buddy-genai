import type { JSX } from 'react';

/**
 * Shown when a guardrail redirected or blocked a turn.
 *
 * The student always sees that something changed and why -- a guard that acts
 * invisibly is indistinguishable from a model that is being evasive.
 */

export interface GuardrailNoticeProps {
  message: string | null;
  onDismiss?: () => void;
}

export function GuardrailNotice({
  message,
  onDismiss,
}: GuardrailNoticeProps): JSX.Element | null {
  if (!message) return null;
  return (
    <div className="notice notice--guardrail" role="status">
      <span className="notice__text">{message}</span>
      {onDismiss ? (
        <button type="button" className="notice__dismiss" onClick={onDismiss} aria-label="Dismiss">
          Got it
        </button>
      ) : null}
    </div>
  );
}
