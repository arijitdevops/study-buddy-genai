/**
 * Renders one turn: markdown with GitHub flavour, KaTeX maths and highlighted
 * code, plus any guardrail notices and verified citations attached to it.
 */

import { memo, type JSX } from 'react';
import ReactMarkdown from 'react-markdown';
import rehypeHighlight from 'rehype-highlight';
import rehypeKatex from 'rehype-katex';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';

import { GuardrailNotice } from './GuardrailNotice';
import { QuizCard } from './QuizCard';
import { TypingIndicator } from './TypingIndicator';
import type { DisplayMessage } from '../types';

export interface MessageBubbleProps {
  message: DisplayMessage;
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
}

function MessageBubbleImpl({ message }: MessageBubbleProps): JSX.Element {
  const isUser = message.role === 'user';
  const showTyping = message.pending && message.content.length === 0;

  return (
    <article className={`bubble ${isUser ? 'bubble--user' : 'bubble--assistant'}`}>
      <span className="bubble__role">{isUser ? 'You' : 'Study Buddy'}</span>

      {(message.notices ?? []).map((notice) => (
        <GuardrailNotice key={notice} message={notice} />
      ))}

      <div className="bubble__body">
        {showTyping ? (
          <TypingIndicator />
        ) : isUser ? (
          <p style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{message.content}</p>
        ) : (
          <ReactMarkdown
            remarkPlugins={[remarkGfm, remarkMath]}
            rehypePlugins={[rehypeKatex, [rehypeHighlight, { detect: true, ignoreMissing: true }]]}
            components={{
              a: ({ href, children, ...rest }) => (
                <a {...rest} href={href} target="_blank" rel="noopener noreferrer">
                  {children}
                </a>
              ),
            }}
          >
            {message.content}
          </ReactMarkdown>
        )}
      </div>

      {message.quiz ? <QuizCard quiz={message.quiz} /> : null}

      {message.citations && message.citations.length > 0 ? (
        <div className="bubble__citations">
          {message.citations.map((url) => (
            <a
              key={url}
              className="bubble__citation"
              href={url}
              target="_blank"
              rel="noopener noreferrer"
            >
              {hostOf(url)}
            </a>
          ))}
        </div>
      ) : null}
    </article>
  );
}

export const MessageBubble = memo(MessageBubbleImpl);
