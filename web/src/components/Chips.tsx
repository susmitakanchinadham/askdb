import type { Chip, ChipKind } from "../api";
import { strings } from "../strings";

interface Props {
  chips: Chip[];
  disabled?: boolean;
  onChange: (kind: ChipKind, value: string) => void;
}

export function Chips({ chips, disabled, onChange }: Props) {
  if (chips.length === 0) return null;
  return (
    <div className="chips">
      {chips.map((chip) => {
        const kind = strings.chipKinds[chip.kind] ?? chip.kind;
        return (
          <div key={chip.kind} role="group" aria-label={strings.chipGroupLabel(kind)}>
            <span className="chip-kind" aria-hidden="true">
              {kind}:
            </span>
            {chip.options.map((option) => (
              <button
                key={option}
                type="button"
                className="chip"
                disabled={disabled}
                onClick={() => onChange(chip.kind, option)}
              >
                {option}
              </button>
            ))}
          </div>
        );
      })}
    </div>
  );
}
