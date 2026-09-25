/** The row of attachment chips above the composer. */

import type { JSX } from 'react';
import { FileUploadChip, type Attachment } from './FileUploadChip';

export interface AttachmentTrayProps {
  attachments: Attachment[];
  onRemove: (localId: string) => void;
}

export function AttachmentTray({
  attachments,
  onRemove,
}: AttachmentTrayProps): JSX.Element | null {
  if (attachments.length === 0) return null;
  return (
    <div className="attachments" aria-label="Attached files">
      {attachments.map((attachment) => (
        <FileUploadChip key={attachment.localId} attachment={attachment} onRemove={onRemove} />
      ))}
    </div>
  );
}
