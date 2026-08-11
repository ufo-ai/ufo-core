import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { tokens } from "@/lib/turnStream";

import { AGENT, CONVO_ID, MEMBER, StreamFake, TURN_ID, json, useStreamFake, wire } from "./harness";

async function streaming() {
  wire({
    "/transcript": () => json({ messages: [] }),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "go" }),
  });
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
  await userEvent.type(screen.getByLabelText("Message the agent"), "go");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  return StreamFake.last();
}

beforeEach(() => {
  useStreamFake();
});

test("a tool frame reports its description, and its tool and preview when it has none", async () => {
  const stream = await streaming();
  stream.emit("tool", { description: "Reading the calendar", tool: "cal", preview: "list" });
  const dock = await screen.findByText("Reading the calendar");
  const dot = dock.parentElement?.querySelector("span[aria-hidden]");
  expect(dot?.className).toContain("animate-working");
  expect(dot?.className).toContain("motion-reduce:animate-none");
  stream.emit("tool", { tool: "bash", preview: "ls -la" });
  expect(await screen.findByText("bash ls -la")).toBeTruthy();
});

test("a skill frame names the skill being loaded", async () => {
  const stream = await streaming();
  stream.emit("skill", { skill: "calendar-triage" });
  expect(await screen.findByText("Loading skill · calendar-triage")).toBeTruthy();
});

test("a settled turn states its latest call and opens onto the ones before it", async () => {
  const stream = await streaming();
  stream.emit("tool", { tool: "bash", preview: "ls" });
  stream.emit("tool", { tool: "read", preview: "notes.md" });
  stream.emit("skill", { skill: "calendar-triage" });
  stream.emit("terminal", {
    status: "done",
    text: "Looked it over.",
    model: "opus",
    tokens: 5,
    cost_micro_usd: 1_000_000,
  });

  const summary = await screen.findByText("Loaded skill · calendar-triage");
  expect(summary.tagName).toBe("SUMMARY");
  expect(screen.queryByText("bash ls")).toBeNull();
  expect(screen.queryByText("Loading skill · calendar-triage")).toBeNull();

  await userEvent.click(summary);
  expect(screen.getByText("bash ls").closest("details")).toBe(summary.closest("details"));
  expect(screen.getByText("read notes.md")).toBeTruthy();
  expect(screen.getAllByText("Loaded skill · calendar-triage")).toHaveLength(2);
});

test("one tool call states itself, with nothing more behind it", async () => {
  const stream = await streaming();
  stream.emit("tool", { tool: "bash", preview: "ls" });
  stream.emit("terminal", {
    status: "done",
    text: "Done.",
    model: "opus",
    tokens: 5,
    cost_micro_usd: 1_000_000,
  });
  const summary = await screen.findByText("bash ls");
  expect(summary.tagName).toBe("SUMMARY");

  await userEvent.click(summary);
  expect(screen.getAllByText("bash ls")).toHaveLength(2);
});

test("a terminal subagent event nests its work under the reply", async () => {
  const stream = await streaming();
  const conversationId = "66666666-6666-4666-8666-666666666666";
  stream.emit("subagent", {
    profile: "general_purpose",
    conversation_id: conversationId,
    events: [{ kind: "note", text: "Reading the changelog." }],
    output: "It shipped Tuesday.",
    subagents: [],
  });
  stream.emit("terminal", {
    status: "done",
    text: "Done.",
    model: "opus",
    tokens: 5,
    cost_micro_usd: 1,
  });

  const summary = await screen.findByText("Reading the changelog.");
  expect(summary.tagName).toBe("SUMMARY");
  expect(screen.queryByRole("link", { name: /Subagent · general_purpose/ })).toBeNull();

  await userEvent.click(summary);
  const card = screen.getByRole("link", { name: /Subagent · general_purpose/ });
  expect(card.getAttribute("href")).toBe(
    "#/subagents/general_purpose/conversations/" + conversationId,
  );
  expect(screen.getByText("It shipped Tuesday.")).toBeTruthy();
});

test("a turn with no tool calls renders no fold", async () => {
  const stream = await streaming();
  stream.emit("terminal", {
    status: "done",
    text: "Just words.",
    model: "opus",
    tokens: 5,
    cost_micro_usd: 1_000_000,
  });
  expect(await screen.findByText("Just words.")).toBeTruthy();
  expect(screen.queryByText(/tool calls?$/)).toBeNull();
});

test("a cost frame meters tokens and priced spend", async () => {
  const stream = await streaming();
  stream.emit("cost", { tokens: 1200, cost_micro_usd: 34500 });
  expect(await screen.findByText("1,200 tok · $0.03")).toBeTruthy();
});

test("a connect frame offers the consent link, and a connect_error states the failure", async () => {
  const stream = await streaming();
  stream.emit("connect", { url: "https://consent.example/go" });
  const link = await screen.findByRole("link", { name: "Connect account" });
  expect(link.getAttribute("href")).toBe("https://consent.example/go");

  stream.emit("connect_error", { message: "The provider refused the request." });
  expect(await screen.findByText("The provider refused the request.")).toBeTruthy();
});

test("a credentials frame arriving mid-stream renders the prompt it asks for", async () => {
  const stream = await streaming();
  stream.emit("credentials", {
    sealed: "seal-1",
    reason: "notion authenticates with this value.",
    prompts: [{ slot: "NOTION_TOKEN", prompt: "the token" }],
  });
  expect(await screen.findByText("notion authenticates with this value.")).toBeTruthy();
  expect(await screen.findByPlaceholderText("NOTION_TOKEN")).toBeTruthy();
});

test("a terminal that is not done states the status and error class it carries", async () => {
  const stream = await streaming();
  stream.emit("terminal", { status: "failed", error_class: "ProviderTimeout" });
  expect(await screen.findByText("(failed: ProviderTimeout)")).toBeTruthy();
  expect(stream.closed).toBe(true);
});

test("a terminal that is not done and names no error class states the status alone", async () => {
  const stream = await streaming();
  stream.emit("terminal", { status: "cancelled" });
  expect(await screen.findByText("(cancelled)")).toBeTruthy();
});

test("a parked turn states why it stopped and closes the stream", async () => {
  const stream = await streaming();
  stream.emit("parked", { message: "Waiting on your answer elsewhere." });
  expect(await screen.findByText("Waiting on your answer elsewhere.")).toBeTruthy();
  expect(stream.closed).toBe(true);
});

test("a done terminal meters the model, tokens, and spend of the turn", async () => {
  const stream = await streaming();
  stream.emit("terminal", {
    status: "done",
    text: "Answered.",
    model: "opus",
    tokens: 800,
    cost_micro_usd: 12000,
  });
  expect(await screen.findByText("opus · 800 tok · $0.01")).toBeTruthy();
  expect(await screen.findByText("Answered.")).toBeTruthy();
});

const ENTRY = {
  question: "Which calendar?",
  options: [{ label: "Work" }, { label: "Home" }],
};

async function asked(entry: Record<string, unknown>) {
  const stream = await streaming();
  stream.emit("terminal", {
    status: "done",
    text: "Asked.",
    model: "opus",
    tokens: 1,
    cost_micro_usd: 1,
    question: { title: "One question", questions: [entry] },
  });
  return stream;
}

test("a single-choice question offers a button per option", async () => {
  await asked(ENTRY);
  expect(await screen.findByRole("button", { name: "Work" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Home" })).toBeTruthy();
});

test("a multi-select question offers no option buttons", async () => {
  await asked({ ...ENTRY, multi_select: true });
  expect(await screen.findByText("Which calendar?")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Work" })).toBeNull();
});

test("a free-text-only question offers no option buttons", async () => {
  await asked({ ...ENTRY, free_text_only: true });
  expect(await screen.findByText("Which calendar?")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Work" })).toBeNull();
});

test("a question allowing attachments offers no option buttons", async () => {
  await asked({ ...ENTRY, allow_attachments: true });
  expect(await screen.findByText("Which calendar?")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Work" })).toBeNull();
});

test("a question with more options than fit offers none of them as buttons", async () => {
  const many = Array.from({ length: 11 }, (_, index) => ({ label: "option-" + index }));
  await asked({ question: "Which one?", options: many });
  expect(await screen.findByText("Which one?")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "option-0" })).toBeNull();
});

test("a send whose answer is not json ends the wait instead of disabling the composer", async () => {
  wire({
    "/transcript": () => json({ messages: [] }),
    "/chat": () => new Response("<html>", { status: 200 }),
  });
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
  await userEvent.type(screen.getByLabelText("Message the agent"), "go");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  expect(await screen.findByText("Network error — try again.")).toBeTruthy();
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Send" }).hasAttribute("disabled")).toBe(false),
  );
});

test("an answer whose response is not json ends the wait rather than hanging", async () => {
  const stream = await streaming();
  stream.emit("terminal", {
    status: "done",
    text: "Asked.",
    model: "opus",
    tokens: 1,
    cost_micro_usd: 1,
    question: { title: "One question", questions: [ENTRY] },
  });
  wire({
    "/transcript": () => json({ messages: [] }),
    "/chat": () => new Response("<html>", { status: 200 }),
  });

  await userEvent.click(await screen.findByRole("button", { name: "Work" }));
  expect(await screen.findByText("Network error — try again.")).toBeTruthy();
});

test("an answer the server refuses states its status rather than hanging", async () => {
  await asked(ENTRY);
  wire({
    "/transcript": () => json({ messages: [] }),
    "/chat": () => new Response("nope", { status: 503 }),
  });

  await userEvent.click(await screen.findByRole("button", { name: "Work" }));
  expect(await screen.findByText("Error 503 — try again.")).toBeTruthy();
});

test("token counts group their thousands", () => {
  expect(tokens(12)).toBe("12");
  expect(tokens(66_473)).toBe("66,473");
});
