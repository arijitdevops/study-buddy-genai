/** Interactive rendering of a generated practice quiz. */

import { useState, type JSX } from 'react';

import type { QuizPayload } from '../types';

export interface QuizCardProps {
  quiz: QuizPayload;
}

export function QuizCard({ quiz }: QuizCardProps): JSX.Element {
  const [chosen, setChosen] = useState<Record<number, string>>({});
  const [revealed, setRevealed] = useState<Record<number, boolean>>({});

  const choose = (index: number, option: string): void => {
    setChosen((current) => ({ ...current, [index]: option }));
    setRevealed((current) => ({ ...current, [index]: true }));
  };

  return (
    <section className="quiz-card" aria-label={`Practice quiz on ${quiz.topic}`}>
      <h3 className="quiz-card__title">Practice quiz: {quiz.topic}</h3>

      {quiz.questions.map((question, index) => {
        const isRevealed = revealed[index] === true;
        const picked = chosen[index];

        return (
          <div className="quiz-card__question" key={`${index}-${question.prompt.slice(0, 24)}`}>
            <p className="quiz-card__prompt">
              {index + 1}. {question.prompt}
            </p>

            {question.kind === 'multiple_choice' && question.options ? (
              <ul className="quiz-card__options">
                {question.options.map((option) => {
                  const isCorrect = option.trim() === question.answer.trim();
                  let modifier = '';
                  if (isRevealed && isCorrect) modifier = ' quiz-card__option--correct';
                  else if (isRevealed && option === picked) modifier = ' quiz-card__option--wrong';

                  return (
                    <li key={option}>
                      <button
                        type="button"
                        className={`quiz-card__option${modifier}`}
                        onClick={() => choose(index, option)}
                        disabled={isRevealed}
                      >
                        {option}
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <button
                type="button"
                className="button button--icon"
                onClick={() => setRevealed((current) => ({ ...current, [index]: true }))}
              >
                {isRevealed ? 'Answer shown' : 'Show answer'}
              </button>
            )}

            {isRevealed ? (
              <p className="quiz-card__explanation">
                <strong>{question.answer}</strong> — {question.explanation}
              </p>
            ) : null}
          </div>
        );
      })}
    </section>
  );
}
