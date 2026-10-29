// Every fixed piece of text the person reads. Business wording only (SPEC 8): the words
// "SQL", "query", "table", "column", "timeout" and "schema" must not appear here.
// tests/strings.test.ts fails when one does.

export const strings = {
  appTitle: "Ask your data",
  appIntro: "Ask a question in plain English. You will get a short answer and the figures behind it.",
  askLabel: "Ask a question",
  askPlaceholder: "For example: What is our current total MRR?",
  askButton: "Ask",
  newQuestion: "New question",
  loading: "Looking that up…",
  cancel: "Cancel",
  examplesHeading: "Try one of these",
  examples: [
    "What is our current total MRR?",
    "Show monthly revenue for 2025.",
    "Which five industries have the most customers?",
  ],
  resultsCaption: "Results",
  asOf: (when: string) => `As of ${when}`,
  rowCount: (total: number) => (total === 1 ? "1 result" : `${total.toLocaleString()} results`),
  showing: (from: number, to: number, total: number) =>
    `Showing ${from.toLocaleString()}–${to.toLocaleString()} of ${total.toLocaleString()}`,
  previousPage: "Previous",
  nextPage: "Next",
  pagerLabel: "Pages of results",
  chipGroupLabel: (kind: string) => `Change the ${kind}`,
  chipKinds: { period: "period", measure: "measure", grouping: "grouping" } as Record<string, string>,
  looksWrong: "This looks wrong",
  feedbackLabel: "What looks wrong? (optional)",
  feedbackSend: "Send",
  feedbackCancel: "Cancel",
  feedbackSending: "Sending…",
  feedbackFailed: "That could not be sent. Please try again.",
  feedbackThanks: "Thanks, we've recorded that so it can be checked.",
  clarifyOr: "Or type your own answer",
  clarifySend: "Use this answer",
  clarifyOptions: "Choose one",
  networkError: "I can't reach the service right now. Please try again shortly.",
  interpretationLabel: "Reading of your question",
  notRecorded: "Not recorded",
} as const;
