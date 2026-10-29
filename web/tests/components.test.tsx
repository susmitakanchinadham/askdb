import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AnswerCard } from "../src/components/AnswerCard";
import { Chips } from "../src/components/Chips";
import { Clarify } from "../src/components/Clarify";
import { ResultTable } from "../src/components/ResultTable";
import { answer, pagedRows } from "./fixtures";

describe("ResultTable", () => {
  const base = {
    columns: ["Company Name", "Total Revenue"],
    rows: pagedRows(1, 25),
    page: 1,
    pageSize: 25,
    total: 1000,
    onPage: vi.fn(),
  };

  it("shows business column names and every cell", () => {
    render(<ResultTable {...base} />);
    expect(screen.getByRole("columnheader", { name: "Company Name" })).toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(26);
    expect(screen.getByRole("cell", { name: "Customer 25" })).toBeInTheDocument();
  });

  it("reports where the page sits and moves forward", async () => {
    const onPage = vi.fn();
    render(<ResultTable {...base} onPage={onPage} />);
    expect(screen.getByText("Showing 1–25 of 1,000")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(onPage).toHaveBeenCalledWith(2);
  });

  it("moves back from a later page", async () => {
    const onPage = vi.fn();
    render(<ResultTable {...base} rows={pagedRows(26, 25)} page={2} onPage={onPage} />);
    expect(screen.getByText("Showing 26–50 of 1,000")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(onPage).toHaveBeenCalledWith(1);
  });

  it("disables Next on the last page", () => {
    render(<ResultTable {...base} rows={pagedRows(976, 25)} page={40} />);
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });

  it("has no pager when everything fits on one page", () => {
    render(<ResultTable {...base} rows={pagedRows(1, 4)} total={4} />);
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });

  it("locks the pager while something is loading", () => {
    render(<ResultTable {...base} disabled />);
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });

  it("renders nothing for an empty result", () => {
    const { container } = render(<ResultTable {...base} rows={[]} total={0} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("Chips", () => {
  const chips = [
    { kind: "period" as const, options: ["2024", "2025"] },
    { kind: "grouping" as const, options: ["by plan", "by industry"] },
  ];

  it("offers each alternative inside a labelled group", () => {
    render(<Chips chips={chips} onChange={vi.fn()} />);
    const group = screen.getByRole("group", { name: "Change the period" });
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual(["2024", "2025"]);
    expect(screen.getByRole("group", { name: "Change the grouping" })).toBeInTheDocument();
  });

  it("reports the kind and value that was picked", async () => {
    const onChange = vi.fn();
    render(<Chips chips={chips} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "by industry" }));
    expect(onChange).toHaveBeenCalledWith("grouping", "by industry");
  });

  it("is disabled as a whole and empty without chips", () => {
    const { rerender, container } = render(<Chips chips={chips} disabled onChange={vi.fn()} />);
    screen.getAllByRole("button").forEach((b) => expect(b).toBeDisabled());
    rerender(<Chips chips={[]} onChange={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("Clarify", () => {
  it("shows the question and each choice, and returns the one clicked", async () => {
    const onChoose = vi.fn();
    render(<Clarify question="Which period?" options={["2024", "2025"]} onChoose={onChoose} />);
    expect(screen.getByText("Which period?")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "2025" }));
    expect(onChoose).toHaveBeenCalledWith("2025");
  });

  it("accepts a typed answer and ignores an empty one", async () => {
    const onChoose = vi.fn();
    render(<Clarify question="Which period?" options={["2024", "2025"]} onChoose={onChoose} />);
    const send = screen.getByRole("button", { name: "Use this answer" });
    expect(send).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Or type your own answer"), " last quarter ");
    await userEvent.click(send);
    expect(onChoose).toHaveBeenCalledWith("last quarter");
  });

  it("locks every control while disabled", () => {
    render(<Clarify question="Which?" options={["a", "b"]} disabled onChoose={vi.fn()} />);
    screen.getAllByRole("button").forEach((b) => expect(b).toBeDisabled());
    expect(screen.getByLabelText("Or type your own answer")).toBeDisabled();
  });
});

describe("AnswerCard", () => {
  const handlers = { onChip: vi.fn(), onPage: vi.fn(), onFeedback: vi.fn() };

  it("shows the sentence, the reading of the question, the figures, the time and the count", () => {
    render(<AnswerCard response={answer()} active busy={false} {...handlers} />);
    expect(screen.getByText("Found 4 results for Active MRR by plan.")).toBeInTheDocument();
    expect(screen.getByText("Active MRR by plan")).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "$4,000" })).toBeInTheDocument();
    expect(screen.getByText(/^As of /)).toHaveTextContent("· 4 results");
  });

  it("shows only the note and the figures when the sentence was withheld", () => {
    render(
      <AnswerCard
        response={answer({ sentence: null, note: "Here are the figures." })}
        active
        busy={false}
        {...handlers}
      />,
    );
    expect(screen.getByText("Here are the figures.")).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  it("says so when there are more than the ceiling", () => {
    render(
      <AnswerCard
        response={answer({
          truncated: true,
          total: 1000,
          truncation_note: "There are more than 1,000 results; try narrowing by period or by a category.",
        })}
        active
        busy={false}
        {...handlers}
      />,
    );
    expect(screen.getByText(/more than 1,000 results/)).toBeInTheDocument();
    expect(screen.getByText(/1,000 results$/)).toBeInTheDocument();
  });

  it("passes chip and page actions up", async () => {
    const onChip = vi.fn();
    const onPage = vi.fn();
    render(
      <AnswerCard
        response={answer({
          chips: [{ kind: "period", options: ["2024", "2025"] }],
          rows: pagedRows(1, 25),
          total: 60,
        })}
        active
        busy={false}
        onChip={onChip}
        onPage={onPage}
        onFeedback={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "2024" }));
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(onChip).toHaveBeenCalledWith("period", "2024");
    expect(onPage).toHaveBeenCalledWith(2);
  });

  it("is read-only when it is not the newest answer", () => {
    render(
      <AnswerCard
        response={answer({
          chips: [{ kind: "period", options: ["2024", "2025"] }],
          rows: pagedRows(1, 25),
          total: 60,
        })}
        active={false}
        busy={false}
        {...handlers}
      />,
    );
    expect(screen.getByRole("button", { name: "2024" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "This looks wrong" })).not.toBeInTheDocument();
  });

  it("sends a 'This looks wrong' note and thanks the person", async () => {
    const onFeedback = vi.fn().mockResolvedValue(true);
    render(<AnswerCard response={answer()} active busy={false} {...handlers} onFeedback={onFeedback} />);
    await userEvent.click(screen.getByRole("button", { name: "This looks wrong" }));
    await userEvent.type(screen.getByLabelText("What looks wrong? (optional)"), "  too low ");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(onFeedback).toHaveBeenCalledWith("too low");
    expect(await screen.findByRole("status")).toHaveTextContent("Thanks, we've recorded that");
  });

  it("allows a report without a note and can be cancelled", async () => {
    const onFeedback = vi.fn().mockResolvedValue(true);
    render(<AnswerCard response={answer()} active busy={false} {...handlers} onFeedback={onFeedback} />);
    await userEvent.click(screen.getByRole("button", { name: "This looks wrong" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: "This looks wrong" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "This looks wrong" }));
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(onFeedback).toHaveBeenCalledWith("");
  });

  it("lets the person try again when the report could not be sent", async () => {
    const onFeedback = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    render(<AnswerCard response={answer()} active busy={false} {...handlers} onFeedback={onFeedback} />);
    await userEvent.click(screen.getByRole("button", { name: "This looks wrong" }));
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be sent");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("status")).toBeInTheDocument();
  });
});
