import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import App from "../src/App";
import { strings } from "../src/strings";
import { answer, clarify, mockApi } from "./fixtures";

// Same list as src/askdb/strings.py (SPEC 8): whole words, plurals and the hyphenated spelling.
const BANNED = /\b(sql|quer(?:y|ies)|tables?|columns?|time-?outs?|schema|stack ?trace)\b/i;

function allTexts(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (typeof value === "function") return allTexts((value as (...a: never[]) => unknown)(...([1, 2, 3] as never[])));
  if (Array.isArray(value)) return value.flatMap(allTexts);
  if (value && typeof value === "object") return Object.values(value).flatMap(allTexts);
  return [String(value)];
}

describe("wording", () => {
  it("no fixed string uses a banned word", () => {
    const offenders = allTexts(strings).filter((t) => BANNED.test(t));
    expect(offenders).toEqual([]);
  });

  it("the detector itself works", () => {
    for (const word of ["SQL", "query", "Queries", "table", "columns", "timeout", "time-out", "schema"]) {
      expect(BANNED.test(`the ${word} here`)).toBe(true);
    }
    expect(BANNED.test("Tablet sales by month")).toBe(false);
  });

  it("nothing visible on screen uses one, across the main states", async () => {
    let n = 0;
    mockApi({
      "/ask": () => {
        n += 1;
        return n === 1 ? answer({ chips: [{ kind: "period", options: ["2024", "2025"] }] }) : clarify();
      },
    });
    const { container } = render(<App />);
    await userEvent.type(screen.getByLabelText("Ask a question"), "Q");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    await screen.findByRole("table");
    await userEvent.click(screen.getByRole("button", { name: "This looks wrong" }));
    // Include accessible names too, since they are read aloud.
    const labels = Array.from(container.querySelectorAll("[aria-label]")).map((e) => e.getAttribute("aria-label")!);
    expect(`${container.textContent} ${labels.join(" ")}`).not.toMatch(BANNED);
  });
});
