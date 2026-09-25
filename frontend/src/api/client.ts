/**
 * Typed fetch wrapper and the SSE reader used by the chat stream.
 *
 * `EventSource` is not used: the chat endpoint is a POST with a JSON body,
 * which `EventSource` cannot send. Instead the response body is read as a
 * stream and parsed with a small SSE frame parser.
 */

import type { ApiErrorBody, ChatStreamEvent } from '../types';

const BASE_URL: string = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');

/** An error carrying the backend's structured error envelope. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: string | null;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.name = 'ApiError';
    this.status = status;
    this.code = body.error;
    this.detail = body.detail ?? null;
  }
}

function url(path: string): string {
  return `${BASE_URL}${path.startsWith('/') ? path : `/${path}`}`;
}

async function toApiError(response: Response): Promise<ApiError> {
  let body: ApiErrorBody = {
    error: `http_${response.status}`,
    message: response.statusText || 'Request failed.',
  };
  try {
    const parsed = (await response.json()) as Partial<ApiErrorBody>;
    if (parsed && typeof parsed.message === 'string') {
      body = {
        error: parsed.error ?? body.error,
        message: parsed.message,
        detail: parsed.detail ?? null,
      };
    }
  } catch {
    // Non-JSON body (a proxy error page, say) - keep the status-based default.
  }
  if (response.status === 429) {
    const retryAfter = response.headers.get('Retry-After');
    if (retryAfter) {
      body.message = `${body.message} (retry in ${retryAfter}s)`;
    }
  }
  return new ApiError(response.status, body);
}

export interface RequestOptions {
  signal?: AbortSignal;
}

/** Perform a JSON request and parse the response. */
export async function request<T>(
  path: string,
  init: RequestInit = {},
  options: RequestOptions = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url(path), {
      ...init,
      signal: options.signal ?? null,
      headers: {
        Accept: 'application/json',
        ...(init.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
        ...(init.headers ?? {}),
      },
    });
  } catch (cause) {
    if ((cause as Error).name === 'AbortError') throw cause;
    throw new ApiError(0, {
      error: 'network_error',
      message: 'Could not reach the Study Buddy server. Is the backend running?',
      detail: (cause as Error).message,
    });
  }

  if (!response.ok) {
    throw await toApiError(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const get = <T>(path: string, options?: RequestOptions): Promise<T> =>
  request<T>(path, { method: 'GET' }, options);

export const post = <T>(path: string, body: unknown, options?: RequestOptions): Promise<T> =>
  request<T>(path, { method: 'POST', body: JSON.stringify(body) }, options);

export const patch = <T>(path: string, body: unknown, options?: RequestOptions): Promise<T> =>
  request<T>(path, { method: 'PATCH', body: JSON.stringify(body) }, options);

export const del = <T>(path: string, options?: RequestOptions): Promise<T> =>
  request<T>(path, { method: 'DELETE' }, options);

export const postForm = <T>(path: string, form: FormData, options?: RequestOptions): Promise<T> =>
  request<T>(path, { method: 'POST', body: form }, options);

/* ------------------------------------------------------------------ */
/* SSE                                                                 */
/* ------------------------------------------------------------------ */

interface RawFrame {
  event: string;
  data: string;
}

/** Split an SSE buffer into complete frames, returning the unconsumed tail. */
function parseFrames(buffer: string): { frames: RawFrame[]; rest: string } {
  const frames: RawFrame[] = [];
  const parts = buffer.split('\n\n');
  const rest = parts.pop() ?? '';

  for (const block of parts) {
    let event = 'message';
    const dataLines: string[] = [];
    for (const line of block.split('\n')) {
      if (line.startsWith(':')) continue; // comment / keep-alive
      if (line.startsWith('event:')) {
        event = line.slice(6).trim();
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trimStart());
      }
    }
    if (dataLines.length > 0) {
      frames.push({ event, data: dataLines.join('\n') });
    }
  }
  return { frames, rest };
}

const KNOWN_EVENTS = new Set(['token', 'tool_call', 'guardrail', 'done', 'error']);

/**
 * POST a body and yield the server's SSE events as they arrive.
 *
 * @param path - API path, e.g. `/api/chat`.
 * @param body - JSON request body.
 * @param signal - Abort signal used to cancel the stream.
 */
export async function* streamEvents(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): AsyncGenerator<ChatStreamEvent, void, void> {
  let response: Response;
  try {
    response = await fetch(url(path), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(body),
      signal: signal ?? null,
    });
  } catch (cause) {
    if ((cause as Error).name === 'AbortError') return;
    throw new ApiError(0, {
      error: 'network_error',
      message: 'Could not reach the Study Buddy server. Is the backend running?',
      detail: (cause as Error).message,
    });
  }

  if (!response.ok) {
    throw await toApiError(response);
  }
  if (!response.body) {
    throw new ApiError(500, {
      error: 'no_stream',
      message: 'The server replied without a readable stream.',
    });
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const { frames, rest } = parseFrames(buffer);
      buffer = rest;

      for (const frame of frames) {
        if (!KNOWN_EVENTS.has(frame.event)) continue;
        try {
          yield { type: frame.event, data: JSON.parse(frame.data) } as ChatStreamEvent;
        } catch {
          // A truncated or malformed frame is dropped rather than killing the
          // whole stream; the `done` event still carries the full answer.
          console.warn('Dropped an unparseable SSE frame', frame);
        }
      }
    }
  } finally {
    reader.releaseLock();
    try {
      await response.body.cancel();
    } catch {
      /* the stream is already closed */
    }
  }
}
