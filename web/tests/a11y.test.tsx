import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { describe, expect, it } from "vitest";
import App from "../src/App";
import { answer, clarify, mockApi, pagedRows } from "./fixtures";

async function ask(text: string) {
  await userEvent.type(screen.getByLabelText("Ask a question"), text);
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
}

describe("accessibility (axe)", () => {
  it("the empty start screen has no violations", async () => {
    mockApi({});
    const { container } = render(<App />);
    expect(await axe(container)).toHaveNoViolations();
  });

  it("an answer with chips, a pager and the report form has no violations", async () => {
    mockApi({
      "/ask": () =>
        answer({
          chips: [
            { kind: "period", options: ["2024", "2025"] },
            { kind: "grouping", options: ["by plan", "by industry"] },
          ],
          rows: pagedRows(1, 25),
          total: 60,
          note: "Here are the figures.",
          sentence: null,
        }),
    });
    const { container } = render(<App />);
    await ask("List customers");
    await userEvent.click(await screen.findByRole("button", { name: "This looks wrong" }));
    expect(await axe(container)).toHaveNoViolations();
  });

  it("a clarifying question, a refusal and a loading state have no violations", async () => {
    let n = 0;
    mockApi({
      "/ask": () => {
        n += 1;
        if (n === 1) return clarify();
        if (n === 2) return { status: "declined", message: "I can't answer that reliably yet." };
        return new Promise(() => {}); // stays loading
      },
    });
    const { container } = render(<App />);
    await ask("Revenue last month?");
    await screen.findByText("Which period do you mean?");
    expect(await axe(container)).toHaveNoViolations();
    await ask("Mark invoices paid");
    await screen.findByText("I can't answer that reliably yet.");
    await ask("Slow one");
    await screen.findByText("Looking that up…");
    expect(await axe(container)).toHaveNoViolations();
  });

  it("an empty result with the thread of several answers has no violations", async () => {
    mockApi({
      "/ask": () => answer({ rows: [], total: 0, sentence: "No matching records found. Try a wider period." }),
    });
    const { container } = render(<App />);
    await ask("Nothing here");
    await screen.findByText(/No matching records found/);
    expect(await axe(container)).toHaveNoViolations();
  });

  it("the checker does flag a real problem, so a pass means something", async () => {
    const { container } = render(
      <div>
        <input type="text" />
        <img src="x.png" />
      </div>,
    );
    const results = await axe(container);
    expect(results.violations.map((v) => v.id)).toEqual(expect.arrayContaining(["label", "image-alt"]));
  });
});
