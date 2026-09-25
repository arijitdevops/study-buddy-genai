/** Chat list with new / rename / delete. */

import { useState, type JSX } from 'react';

import type { ChatSession } from '../types';

export interface SessionSidebarProps {
  sessions: ChatSession[];
  activeId: string | null;
  loading: boolean;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
}

function formatWhen(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function SessionSidebar({
  sessions,
  activeId,
  loading,
  onSelect,
  onCreate,
  onRename,
  onDelete,
}: SessionSidebarProps): JSX.Element {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState('');

  const startEditing = (session: ChatSession): void => {
    setEditingId(session.id);
    setDraft(session.title);
  };

  const commit = (): void => {
    if (editingId && draft.trim()) {
      onRename(editingId, draft.trim());
    }
    setEditingId(null);
  };

  return (
    <aside className="sidebar">
      <div className="sidebar__head">
        <h1 className="sidebar__brand">Study Buddy</h1>
        <button type="button" className="button button--primary button--block" onClick={onCreate}>
          New chat
        </button>
      </div>

      {loading ? (
        <p className="sidebar__empty">
          <span className="spinner-inline" /> Loading chats...
        </p>
      ) : sessions.length === 0 ? (
        <p className="sidebar__empty">No chats yet. Start one above.</p>
      ) : (
        <ul className="sidebar__list">
          {sessions.map((session) => {
            const isActive = session.id === activeId;
            return (
              <li
                key={session.id}
                className={`session-item${isActive ? ' session-item--active' : ''}`}
              >
                {editingId === session.id ? (
                  <input
                    className="session-item__input"
                    value={draft}
                    autoFocus
                    onChange={(event) => setDraft(event.target.value)}
                    onBlur={commit}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') commit();
                      if (event.key === 'Escape') setEditingId(null);
                    }}
                    aria-label="Chat title"
                  />
                ) : (
                  <>
                    <button
                      type="button"
                      className="session-item__button"
                      onClick={() => onSelect(session.id)}
                      aria-current={isActive}
                    >
                      <span className="session-item__title">{session.title}</span>
                      <span className="session-item__meta">
                        {session.subject ? `${session.subject} · ` : ''}
                        {session.message_count} messages · {formatWhen(session.updated_at)}
                      </span>
                    </button>
                    <span className="session-item__actions">
                      <button
                        type="button"
                        className="button button--icon"
                        onClick={() => startEditing(session)}
                        aria-label={`Rename ${session.title}`}
                      >
                        Rename
                      </button>
                      <button
                        type="button"
                        className="button button--icon button--danger"
                        onClick={() => {
                          if (window.confirm(`Delete "${session.title}"? This can't be undone.`)) {
                            onDelete(session.id);
                          }
                        }}
                        aria-label={`Delete ${session.title}`}
                      >
                        Delete
                      </button>
                    </span>
                  </>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </aside>
  );
}
