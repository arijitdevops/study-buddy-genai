/**
 * Drives one chat turn: sends the message, consumes the SSE stream and keeps
 * the transcript, tool indicators and guardrail notices in sync.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError } from '../api/client';
import { sendMessage } from '../api/chat';
import { listMessages } from '../api/sessions';
import type {
  DisplayMessage,
  GuardrailSummary,
  QuizPayload,
  ToolCallEvent,
} from '../types';

function makeId(): string {
  return `local-${Math.random().toString(36).slice(2)}-${Date.now()}`;
}

export interface SendOptions {
  grade: number;
  subject: string | null;
  fileIds: string[];
}

export interface UseChatStreamResult {
  messages: DisplayMessage[];
  streaming: boolean;
  loadingHistory: boolean;
  error: string | null;
  activeTool: ToolCallEvent | null;
  guardrailNotice: string | null;
  send: (text: string, options: SendOptions) => Promise<void>;
  cancel: () => void;
  dismissError: () => void;
  dismissGuardrailNotice: () => void;
}

/**
 * @param sessionId - The active chat session, or null when none is selected.
 * @param onTurnComplete - Called after a turn finishes, so the sidebar can
 *   refresh its counts.
 */
export function useChatStream(
  sessionId: string | null,
  onTurnComplete?: (sessionId: string) => void,
): UseChatStreamResult {
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeTool, setActiveTool] = useState<ToolCallEvent | null>(null);
  const [guardrailNotice, setGuardrailNotice] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);

  // Load history whenever the session changes.
  useEffect(() => {
    let cancelled = false;
    if (!sessionId) {
      setMessages([]);
      return () => {
        cancelled = true;
      };
    }

    setLoadingHistory(true);
    setError(null);
    listMessages(sessionId)
      .then((response) => {
        if (cancelled) return;
        setMessages(
          response.items.map((message) => ({
            id: message.id,
            role: message.role,
            content: message.content,
            createdAt: message.created_at,
          })),
        );
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setError(cause instanceof ApiError ? cause.message : 'Could not load this chat.');
      })
      .finally(() => {
        if (!cancelled) setLoadingHistory(false);
      });

    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  // Abort any in-flight stream on unmount.
  useEffect(() => () => abortRef.current?.abort(), []);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
    setActiveTool(null);
  }, []);

  const send = useCallback(
    async (text: string, options: SendOptions) => {
      const trimmed = text.trim();
      if (!sessionId || !trimmed || streaming) return;

      const controller = new AbortController();
      abortRef.current = controller;

      const userMessage: DisplayMessage = {
        id: makeId(),
        role: 'user',
        content: trimmed,
        createdAt: new Date().toISOString(),
      };
      const assistantId = makeId();
      const assistantMessage: DisplayMessage = {
        id: assistantId,
        role: 'assistant',
        content: '',
        createdAt: new Date().toISOString(),
        pending: true,
      };

      setMessages((current) => [...current, userMessage, assistantMessage]);
      setStreaming(true);
      setError(null);
      setGuardrailNotice(null);
      setActiveTool(null);

      const patchAssistant = (patch: Partial<DisplayMessage>): void => {
        setMessages((current) =>
          current.map((message) =>
            message.id === assistantId ? { ...message, ...patch } : message,
          ),
        );
      };

      let buffer = '';
      let notices: string[] = [];
      let guardrails: GuardrailSummary[] = [];
      let citations: string[] = [];
      let quiz: QuizPayload | null = null;

      try {
        for await (const event of sendMessage(
          {
            session_id: sessionId,
            message: trimmed,
            grade: options.grade,
            subject: options.subject,
            file_ids: options.fileIds,
          },
          controller.signal,
        )) {
          switch (event.type) {
            case 'token':
              buffer += event.data.text;
              patchAssistant({ content: buffer });
              break;

            case 'tool_call':
              setActiveTool(event.data.status === 'done' ? null : event.data);
              break;

            case 'guardrail':
              if (event.data.message) setGuardrailNotice(event.data.message);
              if (event.data.replacement) {
                buffer = event.data.replacement;
                patchAssistant({ content: buffer });
              }
              break;

            case 'done':
              buffer = event.data.answer || buffer;
              notices = event.data.notices;
              guardrails = event.data.guardrail_events;
              citations = event.data.citations;
              quiz = event.data.quiz;
              patchAssistant({
                id: event.data.message_id ?? assistantId,
                content: buffer,
                pending: false,
                notices,
                guardrails,
                citations,
                quiz,
              });
              break;

            case 'error':
              setError(event.data.message);
              patchAssistant({ pending: false, content: buffer });
              break;
          }
        }

        patchAssistant({ pending: false });
        onTurnComplete?.(sessionId);
      } catch (cause) {
        if ((cause as Error).name === 'AbortError') {
          patchAssistant({ pending: false, content: buffer || '*(cancelled)*' });
        } else {
          setError(
            cause instanceof ApiError
              ? cause.message
              : 'Something went wrong while answering. Please try again.',
          );
          patchAssistant({ pending: false, content: buffer });
        }
      } finally {
        setStreaming(false);
        setActiveTool(null);
        abortRef.current = null;
      }
    },
    [sessionId, streaming, onTurnComplete],
  );

  return {
    messages,
    streaming,
    loadingHistory,
    error,
    activeTool,
    guardrailNotice,
    send,
    cancel,
    dismissError: () => setError(null),
    dismissGuardrailNotice: () => setGuardrailNotice(null),
  };
}
