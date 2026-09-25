/** Class (grade) picker. The grade drives the agent's reading level. */

import type { JSX } from 'react';
import { GRADES } from '../types';

export interface GradeSelectorProps {
  value: number;
  onChange: (grade: number) => void;
  disabled?: boolean;
}

export function GradeSelector({ value, onChange, disabled }: GradeSelectorProps): JSX.Element {
  return (
    <label className="selector">
      <span className="selector__label">Class</span>
      <select
        className="selector__control"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
      >
        {GRADES.map((grade) => (
          <option key={grade} value={grade}>
            {grade}
          </option>
        ))}
      </select>
    </label>
  );
}
