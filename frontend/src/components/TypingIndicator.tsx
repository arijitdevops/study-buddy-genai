import type { JSX } from 'react';

/** Three bouncing dots shown while the first token is still in flight. */

export function TypingIndicator(): JSX.Element {
  return (
    <div className="typing" role="status" aria-label="Study Buddy is thinking">
      <span className="typing__dot" />
      <span className="typing__dot" />
      <span className="typing__dot" />
    </div>
  );
}
