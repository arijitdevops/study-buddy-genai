/** Application root: wires the session list, the transcript and the composer. */

import { useCallback, useEffect, useMemo, type JSX } from 'react';

import { ChatWindow } from './components/ChatWindow';
import { ErrorBanner } from './components/ErrorBanner';
import { GradeSelector } from './components/GradeSelector';
import { GuardrailNotice } from './components/GuardrailNotice';
import { Layout } from './components/Layout';
import { MessageComposer } from './components/MessageComposer';
import { SessionSidebar } from './components/SessionSidebar';
import { SubjectSelector } from './components/SubjectSelector';
import { useChatStream } from './hooks/useChatStream';
import { useSessions } from './hooks/useSessions';

export default function App(): JSX.Element {
  const {
    sessions,
    activeId,
    loading: sessionsLoading,
    error: sessionsError,
    grade,
    setGrade,
    selectSession,
    newSession,
    renameSession,
    removeSession,
    setSubject,
    noteActivity,
  } = useSessions();

  const activeSession = useMemo(
    () => sessions.find((session) => session.id === activeId) ?? null,
    [sessions, activeId],
  );

  const {
    messages,
    streaming,
    loadingHistory,
    error: chatError,
    activeTool,
    guardrailNotice,
    send,
    cancel,
    dismissError,
    dismissGuardrailNotice,
  } = useChatStream(activeId, noteActivity);

  // Make sure the student always has somewhere to type.
  useEffect(() => {
    if (!sessionsLoading && sessions.length === 0) {
      void newSession();
    }
  }, [sessionsLoading, sessions.length, newSession]);

  const handleSend = useCallback(
    (text: string, fileIds: string[]) => {
      void send(text, {
        grade,
        subject: activeSession?.subject ?? null,
        fileIds,
      });
    },
    [activeSession?.subject, grade, send],
  );

  const handleSubjectChange = useCallback(
    (subject: string | null) => {
      if (activeId) void setSubject(activeId, subject);
    },
    [activeId, setSubject],
  );

  return (
    <Layout
      headerTitle={activeSession?.title ?? 'Study Buddy'}
      sidebar={
        <SessionSidebar
          sessions={sessions}
          activeId={activeId}
          loading={sessionsLoading}
          onSelect={selectSession}
          onCreate={() => void newSession()}
          onRename={(id, title) => void renameSession(id, title)}
          onDelete={(id) => void removeSession(id)}
        />
      }
      headerControls={
        <>
          <GradeSelector value={grade} onChange={setGrade} disabled={streaming} />
          <SubjectSelector
            value={activeSession?.subject ?? null}
            onChange={handleSubjectChange}
            disabled={streaming || !activeId}
          />
        </>
      }
    >
      <div style={{ padding: '0 var(--space-4)', maxWidth: 'var(--content-max)', margin: '0 auto', width: '100%' }}>
        <ErrorBanner message={sessionsError ?? chatError} onDismiss={dismissError} />
        <GuardrailNotice message={guardrailNotice} onDismiss={dismissGuardrailNotice} />
      </div>

      <ChatWindow
        messages={messages}
        loading={loadingHistory}
        activeTool={activeTool}
        onSuggestion={(text) => handleSend(text, [])}
      />

      <MessageComposer
        sessionId={activeId}
        disabled={sessionsLoading}
        streaming={streaming}
        onSend={handleSend}
        onCancel={cancel}
        onError={(message) => window.alert(message)}
      />
    </Layout>
  );
}
