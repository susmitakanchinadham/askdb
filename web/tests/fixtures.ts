import { vi } from "vitest";
import type { AnswerResponse, ApiResponse, ClarifyResponse } from "../src/api";

export function answer(overrides: Partial<AnswerResponse> = {}): AnswerResponse {
  return {
    status: "answer",
    session_id: "s1",
    interpretation: "Active MRR by plan",
    chips: [],
    sentence: "Found 4 results for Active MRR by plan.",
    note: null,
    columns: ["Plan Name", "Total MRR"],
    rows: [
      ["Starter", "$1,000"],
      ["Professional", "$2,000"],
      ["Business", "$3,000"],
      ["Enterprise", "$4,000"],
    ],
    page: 1,
    page_size: 25,
    total: 4,
    truncated: false,
    truncation_note: null,
    as_of: "2025-11-30T10:00:00+00:00",
    ...overrides,
  };
}

export function pagedRows(from: number, count: number): string[][] {
  return Array.from({ length: count }, (_, i) => [`Customer ${from + i}`, "$10"]);
}

export function clarify(overrides: Partial<ClarifyResponse> = {}): ClarifyResponse {
  return {
    status: "clarify",
    session_id: "s1",
    question: "Which period do you mean?",
    options: ["October 2025", "November 2025"],
    ...overrides,
  };
}

type Handler = (body: Record<string, unknown>) => ApiResponse | Promise<ApiResponse>;

/** Replace fetch with a router keyed by path (without the /api prefix). Returns the call log. */
export function mockApi(routes: Record<string, Handler>) {
  const calls: { path: string; body: Record<string, unknown> }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      const path = url.replace(/^\/api/, "");
      const body = JSON.parse(String(init.body ?? "{}"));
      calls.push({ path, body });
      const handler = routes[path];
      if (!handler) throw new Error(`no route for ${path}`);
      // Like the real fetch, reject when the request is aborted.
      const aborted = new Promise<never>((_, reject) => {
        init.signal?.addEventListener("abort", () =>
          reject(new DOMException("The operation was aborted.", "AbortError")),
        );
      });
      const data = await Promise.race([Promise.resolve(handler(body)), aborted]);
      return { ok: true, status: 200, json: async () => data };
    }),
  );
  return calls;
}
