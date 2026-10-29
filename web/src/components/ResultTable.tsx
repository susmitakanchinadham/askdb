import { strings } from "../strings";

interface Props {
  columns: string[];
  rows: string[][];
  page: number;
  pageSize: number;
  total: number;
  disabled?: boolean;
  onPage: (page: number) => void;
}

export function ResultTable({ columns, rows, page, pageSize, total, disabled, onPage }: Props) {
  if (rows.length === 0) return null;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = (page - 1) * pageSize + 1;
  const to = from + rows.length - 1;

  return (
    <div className="results">
      <div className="table-scroll" tabIndex={0} role="region" aria-label={strings.resultsCaption}>
        <table>
          <caption className="visually-hidden">{strings.resultsCaption}</caption>
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c} scope="col">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, r) => (
              <tr key={r}>
                {row.map((cell, c) => (
                  <td key={c} className={cell === strings.notRecorded ? "muted" : undefined}>
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <nav className="pager" aria-label={strings.pagerLabel}>
          <button type="button" disabled={disabled || page <= 1} onClick={() => onPage(page - 1)}>
            {strings.previousPage}
          </button>
          <span aria-live="polite">{strings.showing(from, to, total)}</span>
          <button
            type="button"
            disabled={disabled || page >= pages}
            onClick={() => onPage(page + 1)}
          >
            {strings.nextPage}
          </button>
        </nav>
      )}
    </div>
  );
}
