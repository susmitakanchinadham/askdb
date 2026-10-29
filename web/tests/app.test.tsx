import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { ApiResponse } from "../src/api";
import App from "../src/App";
import { answer, clarify, mockApi, pagedRows } from "./fixtures";

const chips = [
  { kind: "grouping" as const, options: ["by plan", "by industry"] },
  { kind: "period" as const, options: ["2024", "2025"] },
];

async function ask(text: string) {
  await userEvent.type(screen.getByLabelText("Ask a question"), text);
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
}

describe("asking", () => {
  it("shows the loading text, then the answer", async () => {
    let release!: (r: ApiResponse) => void;
    mockApi({ "/ask": () => new Promise<ApiResponse>((resolve) => (release = resolve)) });
    render(<App />);
    await ask("What is MRR?");
    expect(await screen.findByText("Looking that up…")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "What is MRR?" })).toBeInTheDocument();
    release(answer());
    expect(await screen.findByText("Found 4 results for Active MRR by plan.")).toBeInTheDocument();
    expect(screen.queryByText("Looking that up…")).not.toBeInTheDocument();
  });

  it("starts from example questions", async () => {
    const calls = mockApi({ "/ask": () => answer() });
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "Show monthly revenue for 2025." }));
    await screen.findByRole("table");
    expect(calls[0].body).toEqual({ question: "Show monthly revenue for 2025.", session_id: null });
  });

  it("keeps the session id for the next question", async () => {
    const calls = mockApi({ "/ask": () => answer({ session_id: "abc" }) });
    render(<App />);
    await ask("First");
    await screen.findByRole("table");
    await ask("Second");
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[1].body.session_id).toBe("abc");
  });

  it("cancel stops the request and removes the question", async () => {
    mockApi({ "/ask": () => new Promise<ApiResponse>(() => {}) });
    render(<App />);
    await ask("Slow one");
    await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByText("Looking that up…")).not.toBeInTheDocument());
    expect(screen.queryByRole("heading", { name: "Slow one" })).not.toBeInTheDocument();
  });

  it("explains a refusal in plain words", async () => {
    mockApi({
      "/ask": () => ({ status: "declined", reason: "data_change", message: "I can't answer that reliably yet." }),
    });
    render(<App />);
    await ask("Mark all overdue invoices as paid.");
    expect(await screen.findByText("I can't answer that reliably yet.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("shows a friendly message when the service cannot be reached", async () => {
    const { vi } = await import("vitest");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    render(<App />);
    await ask("Anything");
    expect(await screen.findByText(/can't reach the service/)).toBeInTheDocument();
  });

  it("tells the person when an earlier conversation ended", async () => {
    mockApi({ "/ask": () => answer({ notice: "Your earlier conversation has ended, so I'm starting fresh." }) });
    render(<App />);
    await ask("Again");
    expect(await screen.findByText(/earlier conversation has ended/)).toBeInTheDocument();
  });
});

describe("clarifying", () => {
  it("asks the choice back as a new question", async () => {
    const calls = mockApi({
      "/ask": (body) => (body.question === "November 2025" ? answer() : clarify()),
    });
    render(<App />);
    await ask("How much revenue last month?");
    expect(await screen.findByText("Which period do you mean?")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "November 2025" }));
    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(calls.map((c) => c.body.question)).toEqual(["How much revenue last month?", "November 2025"]);
    expect(calls[1].body.session_id).toBe("s1");
  });
});

describe("changing a chip", () => {
  it("re-runs through the chip endpoint and shows the new answer", async () => {
    const calls = mockApi({
      "/ask": () => answer({ chips }),
      "/ask/chip": () =>
        answer({
          interpretation: "Active MRR by industry",
          sentence: "Found 6 results for Active MRR by industry.",
          chips,
        }),
    });
    render(<App />);
    await ask("MRR");
    await userEvent.click(await screen.findByRole("button", { name: "by industry" }));
    expect(await screen.findByText("Found 6 results for Active MRR by industry.")).toBeInTheDocument();
    expect(calls[1]).toEqual({
      path: "/ask/chip",
      body: { session_id: "s1", kind: "grouping", value: "by industry" },
    });
    expect(screen.getByRole("heading", { name: "grouping: by industry" })).toBeInTheDocument();
  });

  it("only the newest answer keeps live chips", async () => {
    mockApi({ "/ask": () => answer({ chips }) });
    render(<App />);
    await ask("First");
    await screen.findByRole("table");
    await ask("Second");
    await waitFor(() => expect(screen.getAllByRole("table")).toHaveLength(2));
    const [older, newer] = screen.getAllByRole("article");
    expect(within(older).getByRole("button", { name: "by plan" })).toBeDisabled();
    expect(within(newer).getByRole("button", { name: "by plan" })).toBeEnabled();
  });
});

describe("paging", () => {
  it("asks for the next page without sending the question again", async () => {
    const calls = mockApi({
      "/ask": () => answer({ rows: pagedRows(1, 25), total: 60 }),
      "/ask/page": () => answer({ rows: pagedRows(26, 25), total: 60, page: 2 }),
    });
    render(<App />);
    await ask("List customers");
    await userEvent.click(await screen.findByRole("button", { name: "Next" }));
    expect(await screen.findByText("Showing 26–50 of 60")).toBeInTheDocument();
    expect(calls[1]).toEqual({ path: "/ask/page", body: { session_id: "s1", page: 2 } });
    expect(screen.getAllByRole("heading", { name: "List customers" })).toHaveLength(1);
  });
});

describe("feedback and new question", () => {
  it("sends the report for the current session", async () => {
    const calls = mockApi({
      "/ask": () => answer(),
      "/feedback": () => ({ status: "ok", message: "Thanks" }),
    });
    render(<App />);
    await ask("MRR");
    await userEvent.click(await screen.findByRole("button", { name: "This looks wrong" }));
    await userEvent.type(screen.getByLabelText("What looks wrong? (optional)"), "too low");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText(/recorded that/)).toBeInTheDocument();
    expect(calls[1]).toEqual({ path: "/feedback", body: { session_id: "s1", note: "too low" } });
  });

  it("New question clears the thread and resets the conversation", async () => {
    const calls = mockApi({
      "/ask": () => answer(),
      "/session/reset": () => ({ status: "ok", message: "Ready for a new question." }),
    });
    render(<App />);
    await ask("MRR");
    await screen.findByRole("table");
    await userEvent.click(screen.getByRole("button", { name: "New question" }));
    await waitFor(() => expect(screen.queryByRole("table")).not.toBeInTheDocument());
    expect(calls[1]).toEqual({ path: "/session/reset", body: { session_id: "s1" } });
    expect(screen.getByText("Try one of these")).toBeInTheDocument();
  });
});
