import type { JSX } from 'react';

/** Dismissible error banner. */

export interface ErrorBannerProps {
  message: string | null;
  onDismiss?: () => void;
}

export function ErrorBanner({ message, onDismiss }: ErrorBannerProps): JSX.Element | null {
  if (!message) return null;
  return (
    <div className="notice notice--error" role="alert">
      <span className="notice__text">{message}</span>
      {onDismiss ? (
        <button type="button" className="notice__dismiss" onClick={onDismiss} aria-label="Dismiss">
          Dismiss
        </button>
      ) : null}
    </div>
  );
}
