/** File upload endpoints. */

import { del, get, postForm } from './client';
import type { FileUploadResponse, UploadedFile } from '../types';

export const ACCEPTED_EXTENSIONS = ['.pdf', '.docx', '.txt', '.md', '.png', '.jpg', '.jpeg', '.webp'];
export const ACCEPT_ATTRIBUTE = ACCEPTED_EXTENSIONS.join(',');

/** Client-side size cap. The server enforces its own, authoritative limit. */
export const MAX_UPLOAD_BYTES = 15 * 1024 * 1024;

export function uploadFile(
  sessionId: string,
  file: File,
  signal?: AbortSignal,
): Promise<FileUploadResponse> {
  const form = new FormData();
  form.append('session_id', sessionId);
  form.append('file', file);
  return postForm<FileUploadResponse>('/api/files', form, { signal });
}

export function getFile(fileId: string): Promise<UploadedFile> {
  return get<UploadedFile>(`/api/files/${fileId}`);
}

export function deleteFile(fileId: string): Promise<{ message: string }> {
  return del<{ message: string }>(`/api/files/${fileId}`);
}

/** Human-readable file size. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
