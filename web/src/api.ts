import { strings } from "./strings";

export type ChipKind = "period" | "measure" | "grouping";

export interface Chip {
  kind: ChipKind;
  options: string[];
}

interface Common {
  session_id?: string;
  notice?: string;
}

export interface AnswerResponse extends Common {
  status: "answer";
  interpretation: string | null;
  chips: Chip[];
  sentence: string | null;
  note: string | null;
  columns: string[];
  rows: string[][];
  page: number;
  page_size: number;
  total: number;
  truncated: boolean;
  truncation_note: string | null;
  as_of: string;
}

export interface ClarifyResponse extends Common {
  status: "clarify";
  question: string;
  options: string[];
}

export interface MessageResponse extends Common {
  status: "declined" | "unavailable" | "error" | "ok";
  message: string;
  reason?: string;
}

export type ApiResponse = AnswerResponse | ClarifyResponse | MessageResponse;

const BASE = "/api";

function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

async function post(path: string, body: unknown, signal?: AbortSignal): Promise<ApiResponse> {
  try {
    const res = await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
    const data = await res.json();
    if (data && typeof data.status === "string") return data as ApiResponse;
  } catch (err) {
    if (isAbort(err)) throw err; // the person pressed Cancel
  }
  return { status: "error", message: strings.networkError };
}

export const ask = (question: string, sessionId: string | null, signal?: AbortSignal) =>
  post("/ask", { question, session_id: sessionId }, signal);

export const changeChip = (sessionId: string, kind: ChipKind, value: string, signal?: AbortSignal) =>
  post("/ask/chip", { session_id: sessionId, kind, value }, signal);

export const goToPage = (sessionId: string, page: number, signal?: AbortSignal) =>
  post("/ask/page", { session_id: sessionId, page }, signal);

export const sendFeedback = (sessionId: string, note: string) =>
  post("/feedback", { session_id: sessionId, note: note || null });

export const resetSession = (sessionId: string) => post("/session/reset", { session_id: sessionId });
