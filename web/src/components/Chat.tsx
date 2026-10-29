import { useId, useState, type FormEvent } from "react";
import type { ApiResponse, ChipKind } from "../api";
import { strings } from "../strings";
import { AnswerCard } from "./AnswerCard";
import { Clarify } from "./Clarify";

export interface Entry {
  id: number;
  question: string;
  loading: boolean;
  response?: ApiResponse;
}

interface Props {
  entries: Entry[];
  /** Id of the newest answer; only that card accepts chip, page and feedback actions. */
  activeId: number | null;
  busy: boolean;
  onAsk: (question: string) => void;
  onCancel: () => void;
  onChip: (kind: ChipKind, value: string) => void;
  onPage: (entryId: number, page: number) => void;
  onFeedback: (note: string) => Promise<boolean>;
}

export function Chat({ entries, activeId, busy, onAsk, onCancel, onChip, onPage, onFeedback }: Props) {
  const [text, setText] = useState("");
  const inputId = useId();

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const question = text.trim();
    if (question && !busy) {
      onAsk(question);
      setText("");
    }
  };

  const lastId = entries.length ? entries[entries.length - 1].id : null;

  return (
    <div className="chat">
      <div className="thread">
        {entries.length === 0 && (
          <section aria-labelledby="examples-heading" className="examples">
            <h2 id="examples-heading">{strings.examplesHeading}</h2>
            <ul>
              {strings.examples.map((example) => (
                <li key={example}>
                  <button type="button" disabled={busy} onClick={() => onAsk(example)}>
                    {example}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}

        {entries.map((entry) => (
          <section key={entry.id} className="entry" aria-labelledby={`q-${entry.id}`}>
            <h2 id={`q-${entry.id}`} className="question">
              {entry.question}
            </h2>
            {entry.response?.notice && <p role="status" className="notice">{entry.response.notice}</p>}
            {entry.loading && (
              <div className="loading">
                <p role="status">{strings.loading}</p>
                <button type="button" onClick={onCancel}>
                  {strings.cancel}
                </button>
              </div>
            )}
            {entry.response && (
              <Response
                entry={entry}
                active={entry.id === activeId}
                busy={busy}
                // A clarifying question is answered by asking again; only the newest one is live.
                live={entry.id === lastId}
                onAsk={onAsk}
                onChip={onChip}
                onPage={onPage}
                onFeedback={onFeedback}
              />
            )}
          </section>
        ))}
      </div>

      <form className="ask" onSubmit={submit}>
        <label htmlFor={inputId}>{strings.askLabel}</label>
        <div className="ask-row">
          <input
            id={inputId}
            type="text"
            value={text}
            placeholder={strings.askPlaceholder}
            autoComplete="off"
            onChange={(e) => setText(e.target.value)}
          />
          <button type="submit" disabled={busy || text.trim() === ""}>
            {strings.askButton}
          </button>
        </div>
      </form>
    </div>
  );
}

interface ResponseProps {
  entry: Entry;
  active: boolean;
  live: boolean;
  busy: boolean;
  onAsk: (question: string) => void;
  onChip: (kind: ChipKind, value: string) => void;
  onPage: (entryId: number, page: number) => void;
  onFeedback: (note: string) => Promise<boolean>;
}

function Response({ entry, active, live, busy, onAsk, onChip, onPage, onFeedback }: ResponseProps) {
  const response = entry.response!;
  switch (response.status) {
    case "answer":
      return (
        <AnswerCard
          response={response}
          active={active}
          busy={busy || entry.loading}
          onChip={onChip}
          onPage={(page) => onPage(entry.id, page)}
          onFeedback={onFeedback}
        />
      );
    case "clarify":
      return (
        <Clarify
          question={response.question}
          options={response.options}
          disabled={busy || !live}
          onChoose={onAsk}
        />
      );
    default:
      return <p className="message">{response.message}</p>;
  }
}
