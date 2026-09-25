/** Shows which tool the agent is currently using. */

import type { JSX } from 'react';
import type { ToolCallEvent } from '../types';

const LABELS: Record<string, string> = {
  web_search: 'Searching the web',
  wikipedia: 'Reading Wikipedia',
  doc_retrieval: 'Looking through your files',
  calculator: 'Checking the arithmetic',
  quiz: 'Writing quiz questions',
  flashcards: 'Writing flashcards',
};

export interface ToolCallIndicatorProps {
  event: ToolCallEvent | null;
}

export function ToolCallIndicator({ event }: ToolCallIndicatorProps): JSX.Element | null {
  if (!event) return null;
  const label = event.detail || `${LABELS[event.tool] ?? event.tool}...`;
  return (
    <div className="tool-indicator" role="status">
      {event.status === 'running' ? <span className="tool-indicator__spinner" /> : null}
      <span>{label}</span>
    </div>
  );
}
