/** The chat endpoint. */

import { streamEvents } from './client';
import type { ChatRequestBody, ChatStreamEvent } from '../types';

/**
 * Send a message and stream the reply.
 *
 * @param body - The chat request.
 * @param signal - Abort signal for cancelling mid-answer.
 */
export function sendMessage(
  body: ChatRequestBody,
  signal?: AbortSignal,
): AsyncGenerator<ChatStreamEvent, void, void> {
  return streamEvents('/api/chat', body, signal);
}
