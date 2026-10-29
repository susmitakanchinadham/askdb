import { useId, useState, type FormEvent } from "react";
import { strings } from "../strings";

interface Props {
  question: string;
  options: string[];
  disabled?: boolean;
  onChoose: (answer: string) => void;
}

export function Clarify({ question, options, disabled, onChoose }: Props) {
  const [text, setText] = useState("");
  const inputId = useId();

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const answer = text.trim();
    if (answer) {
      onChoose(answer);
      setText("");
    }
  };

  return (
    <div className="clarify">
      <p className="clarify-question">{question}</p>
      <div role="group" aria-label={strings.clarifyOptions} className="clarify-options">
        {options.map((option) => (
          <button key={option} type="button" disabled={disabled} onClick={() => onChoose(option)}>
            {option}
          </button>
        ))}
      </div>
      <form onSubmit={submit} className="clarify-free">
        <label htmlFor={inputId}>{strings.clarifyOr}</label>
        <input
          id={inputId}
          type="text"
          value={text}
          disabled={disabled}
          onChange={(e) => setText(e.target.value)}
        />
        <button type="submit" disabled={disabled || text.trim() === ""}>
          {strings.clarifySend}
        </button>
      </form>
    </div>
  );
}
