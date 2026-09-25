/** One attachment chip, showing upload progress or a rejection reason. */

import type { JSX } from 'react';
import { formatBytes } from '../api/files';
import type { UploadedFile } from '../types';

export interface Attachment {
  localId: string;
  name: string;
  sizeBytes: number;
  status: 'uploading' | 'ready' | 'error';
  file?: UploadedFile;
  error?: string;
  /** Object URL for a local thumbnail when the attachment is an image. */
  previewUrl?: string;
}

export interface FileUploadChipProps {
  attachment: Attachment;
  onRemove: (localId: string) => void;
}

export function FileUploadChip({ attachment, onRemove }: FileUploadChipProps): JSX.Element {
  const modifier =
    attachment.status === 'error'
      ? ' chip--error'
      : attachment.status === 'uploading'
        ? ' chip--uploading'
        : '';

  return (
    <span className={`chip${modifier}`} title={attachment.error ?? attachment.name}>
      {attachment.status === 'uploading' ? <span className="spinner-inline" /> : null}
      {attachment.previewUrl ? (
        <img className="chip__thumb" src={attachment.previewUrl} alt="" aria-hidden="true" />
      ) : null}
      <span className="chip__name">{attachment.name}</span>
      <span>{attachment.status === 'error' ? 'failed' : formatBytes(attachment.sizeBytes)}</span>
      <button
        type="button"
        className="chip__remove"
        onClick={() => onRemove(attachment.localId)}
        aria-label={`Remove ${attachment.name}`}
      >
        ×
      </button>
    </span>
  );
}
