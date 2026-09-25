/**
 * Shared domain types.
 *
 * These mirror the Pydantic models in `backend/app/schemas`. When you change
 * one side, change the other.
 */

/** School class, 1-12. */
export type Grade = 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12;

export const GRADES: readonly Grade[] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];

export const SUBJECTS = [
  'Mathematics',
  'Physics',
  'Chemistry',
  'Biology',
  'Science',
  'Computer Science',
  'English',
  'History',
  'Geography',
  'Economics',
  'Languages',
] as const;

export type Subject = (typeof SUBJECTS)[number];

export type MessageRole = 'user' | 'assistant' | 'system' | 'tool';

export type GuardrailVerdict = 'allow' | 'soft_block' | 'hard_block';

export type ToolStatus = 'running' | 'done' | 'error';

export interface ChatSession {
  id: string;
  student_id: string;
  title: string;
  subject: string | null;
  grade: number;
  message_count: number;
  file_count: number;
  created_at: string;
  updated_at: string;
}

export interface SessionListResponse {
  items: ChatSession[];
  total: number;
}

export interface ChatMessage {
  id: string;
  session_id: string;
  role: MessageRole;
  content: string;
  token_count: number;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface MessageListResponse {
  items: ChatMessage[];
  total: number;
}

export interface UploadedFile {
  id: string;
  session_id: string;
  original_name: string;
  mime: string;
  size_bytes: number;
  extracted_chars: number;
  status: 'pending' | 'extracting' | 'ready' | 'failed';
  error: string | null;
  chunk_count: number;
  is_image: boolean;
  created_at: string;
}

export interface FileUploadResponse {
  file: UploadedFile;
  message: string;
}

/** A quiz question as produced by the agent's quiz node. */
export interface QuizQuestion {
  prompt: string;
  kind: 'multiple_choice' | 'short_answer';
  options?: string[];
  answer: string;
  explanation: string;
  difficulty?: 'easy' | 'medium' | 'hard';
}

export interface QuizPayload {
  topic: string;
  questions: QuizQuestion[];
}

/* ------------------------------------------------------------------ */
/* SSE events                                                          */
/* ------------------------------------------------------------------ */

export interface TokenEvent {
  text: string;
}

export interface ToolCallEvent {
  tool: string;
  status: ToolStatus;
  detail: string;
}

export interface GuardrailEvent {
  guard: string;
  verdict: GuardrailVerdict;
  category?: string | null;
  message: string;
  /** When present, replaces everything streamed so far. */
  replacement?: string | null;
}

export interface ToolResultSummary {
  tool: string;
  query: string;
  ok: boolean;
  detail: string;
  urls: string[];
}

export interface GuardrailSummary {
  guard: string;
  verdict: string;
  category?: string | null;
  detail: string;
}

export interface DoneEvent {
  answer: string;
  message_id: string | null;
  intent: string | null;
  blocked: boolean;
  notices: string[];
  tool_results: ToolResultSummary[];
  guardrail_events: GuardrailSummary[];
  quiz: QuizPayload | null;
  citations: string[];
}

export interface ErrorEvent {
  message: string;
  detail?: string | null;
}

/** A discriminated union over every event the chat stream can send. */
export type ChatStreamEvent =
  | { type: 'token'; data: TokenEvent }
  | { type: 'tool_call'; data: ToolCallEvent }
  | { type: 'guardrail'; data: GuardrailEvent }
  | { type: 'done'; data: DoneEvent }
  | { type: 'error'; data: ErrorEvent };

export interface ChatRequestBody {
  session_id: string;
  message: string;
  grade?: number;
  subject?: string | null;
  file_ids?: string[];
}

export interface ApiErrorBody {
  error: string;
  message: string;
  detail?: string | null;
}

/** A turn as rendered in the UI, including in-flight state. */
export interface DisplayMessage {
  id: string;
  role: MessageRole;
  content: string;
  createdAt: string;
  pending?: boolean;
  notices?: string[];
  guardrails?: GuardrailSummary[];
  citations?: string[];
  quiz?: QuizPayload | null;
}
