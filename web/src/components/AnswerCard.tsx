import { useId, useState, type FormEvent } from "react";
import type { AnswerResponse, ChipKind } from "../api";
import { strings } from "../strings";
import { Chips } from "./Chips";
import { ResultTable } from "./ResultTable";

interface Props {
  response: AnswerResponse;
  /** Only the newest answer can be changed, paged or reported; older cards are read-only. */
  active: boolean;
  busy: boolean;
  onChip: (kind: ChipKind, value: string) => void;
  onPage: (page: number) => void;
  onFeedback: (note: string) => Promise<boolean>;
}

type FeedbackState = "closed" | "open" | "sending" | "sent" | "failed";

export function AnswerCard({ response, active, busy, onChip, onPage, onFeedback }: Props) {
  const [feedback, setFeedback] = useState<FeedbackState>("closed");
  const [note, setNote] = useState("");
  const noteId = useId();
  const locked = !active || busy;

  const send = async (event: FormEvent) => {
    event.preventDefault();
    setFeedback("sending");
    setFeedback((await onFeedback(note.trim())) ? "sent" : "failed");
  };

  const when = new Date(response.as_of).toLocaleString();

  return (
    <article className="answer">
      {response.sentence && <p className="sentence">{response.sentence}</p>}
      {response.note && <p className="muted">{response.note}</p>}
      {response.interpretation && (
        <p className="interpretation">
          <span className="visually-hidden">{strings.interpretationLabel}: </span>
          {response.interpretation}
        </p>
      )}
      <Chips chips={response.chips} disabled={locked} onChange={onChip} />
      <ResultTable
        columns={response.columns}
        rows={response.rows}
        page={response.page}
        pageSize={response.page_size}
        total={response.total}
        disabled={locked}
        onPage={onPage}
      />
      {response.truncation_note && response.truncation_note !== response.sentence && (
        <p className="muted">{response.truncation_note}</p>
      )}
      <p className="meta">
        {strings.asOf(when)} · {strings.rowCount(response.total)}
      </p>

      {active && feedback === "closed" && (
        <button type="button" className="link" onClick={() => setFeedback("open")}>
          {strings.looksWrong}
        </button>
      )}
      {(feedback === "open" || feedback === "sending" || feedback === "failed") && (
        <form className="feedback" onSubmit={send}>
          <label htmlFor={noteId}>{strings.feedbackLabel}</label>
          <textarea
            id={noteId}
            value={note}
            maxLength={500}
            rows={2}
            onChange={(e) => setNote(e.target.value)}
          />
          <button type="submit" disabled={feedback === "sending"}>
            {feedback === "sending" ? strings.feedbackSending : strings.feedbackSend}
          </button>
          <button type="button" onClick={() => setFeedback("closed")}>
            {strings.feedbackCancel}
          </button>
          {feedback === "failed" && <p role="alert">{strings.feedbackFailed}</p>}
        </form>
      )}
      {feedback === "sent" && <p role="status">{strings.feedbackThanks}</p>}
    </article>
  );
}
