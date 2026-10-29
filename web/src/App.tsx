import { useRef, useState } from "react";
import * as api from "./api";
import { Chat, type Entry } from "./components/Chat";
import { strings } from "./strings";

export default function App() {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const nextId = useRef(1);
  const running = useRef<AbortController | null>(null);

  const busy = entries.some((e) => e.loading);
  const activeId = [...entries].reverse().find((e) => e.response?.status === "answer")?.id ?? null;

  /** Run one request. A new question adds an entry; a page change reloads an existing one. */
  const run = async (
    call: (signal: AbortSignal) => Promise<api.ApiResponse>,
    options: { question: string; entryId?: number },
  ) => {
    const controller = new AbortController();
    running.current = controller;
    const id = options.entryId ?? nextId.current++;
    const reload = options.entryId !== undefined;

    setEntries((prev) =>
      reload
        ? prev.map((e) => (e.id === id ? { ...e, loading: true } : e))
        : [...prev, { id, question: options.question, loading: true }],
    );
    try {
      const response = await call(controller.signal);
      if (response.session_id) setSessionId(response.session_id);
      setEntries((prev) => prev.map((e) => (e.id === id ? { ...e, loading: false, response } : e)));
    } catch {
      // Cancelled: drop a new entry, keep an existing one as it was.
      setEntries((prev) =>
        reload ? prev.map((e) => (e.id === id ? { ...e, loading: false } : e)) : prev.filter((e) => e.id !== id),
      );
    } finally {
      if (running.current === controller) running.current = null;
    }
  };

  const onAsk = (question: string) => run((signal) => api.ask(question, sessionId, signal), { question });

  const onChip = (kind: api.ChipKind, value: string) =>
    sessionId &&
    run((signal) => api.changeChip(sessionId, kind, value, signal), {
      question: `${strings.chipKinds[kind] ?? kind}: ${value}`,
    });

  const onPage = (entryId: number, page: number) =>
    sessionId &&
    run((signal) => api.goToPage(sessionId, page, signal), {
      question: "",
      entryId,
    });

  const onFeedback = async (note: string) => {
    if (!sessionId) return false;
    return (await api.sendFeedback(sessionId, note)).status === "ok";
  };

  const onCancel = () => running.current?.abort();

  const onNewQuestion = async () => {
    running.current?.abort();
    if (sessionId) await api.resetSession(sessionId);
    setEntries([]);
  };

  return (
    <div className="app">
      <header>
        <h1>{strings.appTitle}</h1>
        <button type="button" onClick={onNewQuestion} disabled={entries.length === 0 && !busy}>
          {strings.newQuestion}
        </button>
      </header>
      <main>
        {entries.length === 0 && <p className="intro">{strings.appIntro}</p>}
        <Chat
          entries={entries}
          activeId={activeId}
          busy={busy}
          onAsk={onAsk}
          onCancel={onCancel}
          onChip={onChip}
          onPage={onPage}
          onFeedback={onFeedback}
        />
      </main>
    </div>
  );
}
