/** Session endpoints. */

import { del, get, patch, post } from './client';
import type { ChatSession, MessageListResponse, SessionListResponse } from '../types';

export interface CreateSessionInput {
  title?: string;
  subject?: string | null;
  grade?: number;
  student_id?: string | null;
  display_name?: string;
}

export function createSession(input: CreateSessionInput = {}): Promise<ChatSession> {
  return post<ChatSession>('/api/sessions', {
    title: input.title ?? 'New chat',
    subject: input.subject ?? null,
    grade: input.grade ?? 8,
    student_id: input.student_id ?? null,
    display_name: input.display_name ?? 'Student',
  });
}

export function listSessions(studentId?: string | null): Promise<SessionListResponse> {
  const query = studentId ? `?student_id=${encodeURIComponent(studentId)}` : '';
  return get<SessionListResponse>(`/api/sessions${query}`);
}

export function getSession(sessionId: string): Promise<ChatSession> {
  return get<ChatSession>(`/api/sessions/${sessionId}`);
}

export interface UpdateSessionInput {
  title?: string;
  subject?: string | null;
  grade?: number;
}

export function updateSession(
  sessionId: string,
  input: UpdateSessionInput,
): Promise<ChatSession> {
  return patch<ChatSession>(`/api/sessions/${sessionId}`, input);
}

export function deleteSession(sessionId: string): Promise<{ message: string }> {
  return del<{ message: string }>(`/api/sessions/${sessionId}`);
}

export function listMessages(sessionId: string): Promise<MessageListResponse> {
  return get<MessageListResponse>(`/api/sessions/${sessionId}/messages`);
}
