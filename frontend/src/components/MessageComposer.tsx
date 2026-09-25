/**
 * The input row: auto-growing textarea, attachment picker and send button.
 *
 * Enter sends, Shift+Enter inserts a newline.
 */

import { useCallback, useEffect, useRef, useState, type JSX } from 'react';

import { ACCEPT_ATTRIBUTE, MAX_UPLOAD_BYTES, deleteFile, uploadFile } from '../api/files';
import { ApiError } from '../api/client';
import { AttachmentTray } from './AttachmentTray';
import type { Attachment } from './FileUploadChip';

const MAX_ATTACHMENTS = 3;

function makeLocalId(): string {
  return `att-${Math.random().toString(36).slice(2)}`;
}

export interface MessageComposerProps {
  sessionId: string | null;
  disabled: boolean;
  streaming: boolean;
  onSend: (text: string, fileIds: string[]) => void;
  onCancel: () => void;
  onError: (message: string) => void;
}

export function MessageComposer({
  sessionId,
  disabled,
  streaming,
  onSend,
  onCancel,
  onError,
}: MessageComposerProps): JSX.Element {
  const [text, setText] = useState('');
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Grow the textarea with its content, up to the CSS max-height.
  useEffect(() => {
    const node = textareaRef.current;
    if (!node) return;
    node.style.height = 'auto';
    node.style.height = `${Math.min(node.scrollHeight, 200)}px`;
  }, [text]);

  const handleFiles = useCallback(
    async (files: FileList | null) => {
      if (!files || !sessionId) return;
      const room = MAX_ATTACHMENTS - attachments.length;
      if (room <= 0) {
        onError(`You can attach up to ${MAX_ATTACHMENTS} files per message.`);
        return;
      }

      for (const file of Array.from(files).slice(0, room)) {
        const localId = makeLocalId();
        if (file.size > MAX_UPLOAD_BYTES) {
          setAttachments((current) => [
            ...current,
            {
              localId,
              name: file.name,
              sizeBytes: file.size,
              status: 'error',
              error: 'That file is larger than the 15 MB limit.',
            },
          ]);
          continue;
        }

        const previewUrl = file.type.startsWith('image/') ? URL.createObjectURL(file) : undefined;
        setAttachments((current) => [
          ...current,
          { localId, name: file.name, sizeBytes: file.size, status: 'uploading', previewUrl },
        ]);

        try {
          const response = await uploadFile(sessionId, file);
          setAttachments((current) =>
            current.map((attachment) =>
              attachment.localId === localId
                ? {
                    ...attachment,
                    status: response.file.status === 'failed' ? 'error' : 'ready',
                    file: response.file,
                    error: response.file.error ?? undefined,
                  }
                : attachment,
            ),
          );
          if (response.file.status === 'failed') {
            onError(response.message);
          }
        } catch (cause) {
          const message =
            cause instanceof ApiError ? cause.message : 'That file could not be uploaded.';
          setAttachments((current) =>
            current.map((attachment) =>
              attachment.localId === localId
                ? { ...attachment, status: 'error', error: message }
                : attachment,
            ),
          );
          onError(message);
        }
      }
    },
    [attachments.length, onError, sessionId],
  );

  const removeAttachment = useCallback((localId: string) => {
    setAttachments((current) => {
      const target = current.find((attachment) => attachment.localId === localId);
      if (target?.previewUrl) URL.revokeObjectURL(target.previewUrl);
      if (target?.file) {
        // Fire-and-forget: a failed cleanup leaves a harmless orphan row.
        void deleteFile(target.file.id).catch(() => undefined);
      }
      return current.filter((attachment) => attachment.localId !== localId);
    });
  }, []);

  const submit = useCallback(() => {
    const trimmed = text.trim();
    if (!trimmed || disabled || streaming) return;
    const fileIds = attachments
      .filter((attachment) => attachment.status === 'ready' && attachment.file)
      .map((attachment) => attachment.file!.id);
    onSend(trimmed, fileIds);
    setText('');
    attachments.forEach((attachment) => {
      if (attachment.previewUrl) URL.revokeObjectURL(attachment.previewUrl);
    });
    setAttachments([]);
    if (fileInputRef.current) fileInputRef.current.value = '';
  }, [attachments, disabled, onSend, streaming, text]);

  return (
    <div className="composer">
      <div className="composer__inner">
        <AttachmentTray attachments={attachments} onRemove={removeAttachment} />

        <div className="composer__row">
          <input
            ref={fileInputRef}
            type="file"
            className="visually-hidden"
            accept={ACCEPT_ATTRIBUTE}
            multiple
            onChange={(event) => void handleFiles(event.target.files)}
          />
          <button
            type="button"
            className="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={disabled || !sessionId}
            title="Attach a PDF, Word file, notes or a photo"
          >
            Attach
          </button>

          <textarea
            ref={textareaRef}
            className="composer__textarea"
            value={text}
            placeholder={
              sessionId ? 'Ask anything about your schoolwork...' : 'Start a chat to begin'
            }
            disabled={disabled || !sessionId}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                submit();
              }
            }}
          />

          {streaming ? (
            <button type="button" className="button" onClick={onCancel}>
              Stop
            </button>
          ) : (
            <button
              type="button"
              className="button button--primary"
              onClick={submit}
              disabled={disabled || !sessionId || text.trim().length === 0}
            >
              Send
            </button>
          )}
        </div>

        <span className="composer__hint">
          Enter to send, Shift+Enter for a new line. Study Buddy can make mistakes — check
          anything important.
        </span>
      </div>
    </div>
  );
}
