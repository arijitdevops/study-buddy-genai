/** The scrolling transcript. */

import { useEffect, useRef, type JSX } from 'react';

import { MessageBubble } from './MessageBubble';
import { ToolCallIndicator } from './ToolCallIndicator';
import type { DisplayMessage, ToolCallEvent } from '../types';

const SUGGESTIONS = [
  'Explain photosynthesis with an example',
  'Solve 3x + 5 = 20 step by step',
  'Quiz me on the water cycle',
  'Make flashcards for the periodic table',
];

export interface ChatWindowProps {
  messages: DisplayMessage[];
  loading: boolean;
  activeTool: ToolCallEvent | null;
  onSuggestion: (text: string) => void;
}

export function ChatWindow({
  messages,
  loading,
  activeTool,
  onSuggestion,
}: ChatWindowProps): JSX.Element {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' });
  }, [messages, activeTool]);

  if (loading) {
    return (
      <div className="chat">
        <div className="chat__empty">
          <span className="spinner-inline" /> Loading this chat...
        </div>
      </div>
    );
  }

  if (messages.length === 0) {
    return (
      <div className="chat">
        <div className="chat__empty">
          <h2>What are we studying today?</h2>
          <p>
            Pick your class above so I explain things at the right level, then ask me
            anything — or upload your notes and ask about those.
          </p>
          <div className="chat__suggestions">
            {SUGGESTIONS.map((suggestion) => (
              <button
                key={suggestion}
                type="button"
                className="chat__suggestion"
                onClick={() => onSuggestion(suggestion)}
              >
                {suggestion}
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="chat">
      <div className="chat__inner">
        {messages.map((message) => (
          <MessageBubble key={message.id} message={message} />
        ))}
        <ToolCallIndicator event={activeTool} />
        <div ref={endRef} />
      </div>
    </div>
  );
}
