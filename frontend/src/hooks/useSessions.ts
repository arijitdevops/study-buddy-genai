/** Session list state: loading, creating, renaming and deleting chats. */

import { useCallback, useEffect, useRef, useState } from 'react';

import {
  createSession,
  deleteSession as deleteSessionApi,
  listSessions,
  updateSession,
} from '../api/sessions';
import { ApiError } from '../api/client';
import type { ChatSession } from '../types';

const STUDENT_ID_KEY = 'study-buddy.student-id';
const GRADE_KEY = 'study-buddy.grade';

function readStored(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStored(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* private browsing - the app still works, it just forgets */
  }
}

export interface UseSessionsResult {
  sessions: ChatSession[];
  activeId: string | null;
  loading: boolean;
  error: string | null;
  grade: number;
  setGrade: (grade: number) => void;
  selectSession: (id: string) => void;
  newSession: (subject?: string | null) => Promise<ChatSession | null>;
  renameSession: (id: string, title: string) => Promise<void>;
  removeSession: (id: string) => Promise<void>;
  setSubject: (id: string, subject: string | null) => Promise<void>;
  refresh: () => Promise<void>;
  noteActivity: (id: string) => void;
}

/** Owns the sidebar's session list and the active-session selection. */
export function useSessions(): UseSessionsResult {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [grade, setGradeState] = useState<number>(() => {
    const stored = Number(readStored(GRADE_KEY));
    return Number.isInteger(stored) && stored >= 1 && stored <= 12 ? stored : 8;
  });

  const studentIdRef = useRef<string | null>(readStored(STUDENT_ID_KEY));

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await listSessions(studentIdRef.current);
      setSessions(response.items);
      setActiveId((current) => current ?? response.items[0]?.id ?? null);
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : 'Could not load your chats.',
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const setGrade = useCallback((next: number) => {
    setGradeState(next);
    writeStored(GRADE_KEY, String(next));
  }, []);

  const newSession = useCallback(
    async (subject: string | null = null): Promise<ChatSession | null> => {
      setError(null);
      try {
        const created = await createSession({
          title: 'New chat',
          subject,
          grade,
          student_id: studentIdRef.current,
        });
        studentIdRef.current = created.student_id;
        writeStored(STUDENT_ID_KEY, created.student_id);
        setSessions((current) => [created, ...current]);
        setActiveId(created.id);
        return created;
      } catch (cause) {
        setError(cause instanceof ApiError ? cause.message : 'Could not start a new chat.');
        return null;
      }
    },
    [grade],
  );

  const renameSession = useCallback(async (id: string, title: string) => {
    const trimmed = title.trim();
    if (!trimmed) return;
    const previous = sessions;
    setSessions((current) =>
      current.map((session) => (session.id === id ? { ...session, title: trimmed } : session)),
    );
    try {
      await updateSession(id, { title: trimmed });
    } catch (cause) {
      setSessions(previous);
      setError(cause instanceof ApiError ? cause.message : 'Could not rename that chat.');
    }
  }, [sessions]);

  const setSubject = useCallback(async (id: string, subject: string | null) => {
    setSessions((current) =>
      current.map((session) => (session.id === id ? { ...session, subject } : session)),
    );
    try {
      await updateSession(id, { subject });
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : 'Could not update the subject.');
    }
  }, []);

  const removeSession = useCallback(
    async (id: string) => {
      const previous = sessions;
      setSessions((current) => current.filter((session) => session.id !== id));
      setActiveId((current) =>
        current === id ? previous.find((s) => s.id !== id)?.id ?? null : current,
      );
      try {
        await deleteSessionApi(id);
      } catch (cause) {
        setSessions(previous);
        setError(cause instanceof ApiError ? cause.message : 'Could not delete that chat.');
      }
    },
    [sessions],
  );

  const noteActivity = useCallback((id: string) => {
    setSessions((current) =>
      current.map((session) =>
        session.id === id
          ? { ...session, updated_at: new Date().toISOString(), message_count: session.message_count + 2 }
          : session,
      ),
    );
  }, []);

  return {
    sessions,
    activeId,
    loading,
    error,
    grade,
    setGrade,
    selectSession: setActiveId,
    newSession,
    renameSession,
    removeSession,
    setSubject,
    refresh,
    noteActivity,
  };
}
