/** Optional subject focus for the current chat. */

import type { JSX } from 'react';
import { SUBJECTS } from '../types';

export interface SubjectSelectorProps {
  value: string | null;
  onChange: (subject: string | null) => void;
  disabled?: boolean;
}

export function SubjectSelector({
  value,
  onChange,
  disabled,
}: SubjectSelectorProps): JSX.Element {
  return (
    <label className="selector">
      <span className="selector__label">Subject</span>
      <select
        className="selector__control"
        value={value ?? ''}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value || null)}
      >
        <option value="">Any</option>
        {SUBJECTS.map((subject) => (
          <option key={subject} value={subject}>
            {subject}
          </option>
        ))}
      </select>
    </label>
  );
}
