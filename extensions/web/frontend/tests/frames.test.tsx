import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { tokens } from "@/lib/turnStream";

import {
  AGENT,
  ARRIVAL_ID,
  CHAT_ROW,
  CONVO_ID,
  MEMBER,
  SECOND_ID,
  StreamFake,
  TURN_ID,
  json,
  saying,
  useStreamFake,
  wire,
} from "./harness";

async function streaming() {
  wire({
    "/transcript": () => json({ messages: [] }),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "go" }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await userEvent.type(screen.getByLabelText("Message the agent"), "go");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  return StreamFake.last();
}

/** A browser queues the `toggle` for an `open` a render wrote in a task of its own; awaiting a timer
 *  of the same kind lets it land before the next frame is emitted. */
const delivered = () =>
  act(async () => void (await new Promise((resolve) => setTimeout(resolve, 0))));

beforeEach(() => {
  useStreamFake();
});

test("a tool frame decodes its complete description, and uses its tool and preview when absent", async () => {
  const stream = await streaming();
  stream.emit("tool", { description: "Reading the calendar", tool: "cal", preview: "list" });
  // The open fold states the step too, so the line is the one reading it out.
  const dock = await screen.findByText("Reading the calendar", { selector: ".sr-only" });
  const decoded = dock.parentElement!.querySelector("[data-slot=decode-text]")!;
  expect(decoded.textContent).toHaveLength("Reading the calendar".length);
  expect(decoded.textContent).not.toBe("Reading the calendar");
  expect(decoded.textContent?.replaceAll(" ", "")).not.toMatch(/[A-Za-z]/);
  // A space is a break between words rather than a cell, so it is the only character without one.
  expect(decoded.querySelectorAll("[data-slot=decode-cell]")).toHaveLength(
    "Reading the calendar".replaceAll(" ", "").length,
  );
  expect(decoded.querySelector("[data-slot=decode-cell]")?.className).toContain(
    "w-(--size-decode-cell)",
  );
  stream.emit("tool", { tool: "bash", preview: "ls -la" });
  expect(await screen.findByText("bash ls -la", { selector: ".sr-only" })).toBeTruthy();
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

  const summary = await screen.findByText("Completed 3 steps");
  expect(summary.closest("summary")).toBeTruthy();
  expect(screen.queryByText("bash ls")).toBeNull();
  expect(screen.queryByText("Loading skill · calendar-triage")).toBeNull();

  await userEvent.click(summary);
  expect(screen.getByText("bash ls").closest("details")).toBe(summary.closest("details"));
  expect(screen.getByText("read notes.md")).toBeTruthy();
  expect(screen.getByText("Loaded skill · calendar-triage")).toBeTruthy();
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
  const summary = await screen.findByText("Completed 1 step");
  expect(summary.closest("summary")).toBeTruthy();
  expect(screen.queryByText("bash ls")).toBeNull();

  await userEvent.click(summary);
  expect(screen.getByText("bash ls")).toBeTruthy();
});

test("a thought a tool call interrupted becomes a step, in the order it was written", async () => {
  const stream = await streaming();
  stream.emit("message", { text: "Reading the changelog first." });
  expect(await screen.findByText(saying("Reading the changelog first."))).toBeTruthy();

  stream.emit("tool", { tool: "read", preview: "CHANGELOG.md" });
  stream.emit("message", { text: "It shipped Tuesday." });
  stream.emit("terminal", {
    status: "done",
    text: "It shipped Tuesday.",
    model: "opus",
    tokens: 5,
    cost_micro_usd: 1_000_000,
  });

  const summary = await screen.findByText("Completed 2 steps");
  expect(screen.queryByText("Reading the changelog first.")).toBeNull();
  expect(screen.getByText("It shipped Tuesday.")).toBeTruthy();

  await userEvent.click(summary);
  const rows = summary.closest("details")!.querySelectorAll("li");
  expect([...rows].map((row) => row.textContent)).toEqual([
    "Reading the changelog first.",
    "read CHANGELOG.md",
  ]);
});

test("the fold a running turn writes into stands open on its thoughts among its calls", async () => {
  const stream = await streaming();
  stream.emit("message", { text: "Reading the changelog first." });
  stream.emit("tool", { tool: "read", preview: "CHANGELOG.md" });
  await waitFor(() =>
    expect(screen.queryByText(saying("Reading the changelog first."))).toBeNull(),
  );

  const fold = document.querySelector("details") as HTMLDetailsElement;
  await waitFor(() => expect(fold.open).toBe(true));

  stream.emit("message", { text: "Checking the tags now." });
  stream.emit("tool", { tool: "bash", preview: "git tag" });
  await waitFor(() =>
    expect([...fold.querySelectorAll("li")].map((row) => row.textContent)).toEqual([
      "Reading the changelog first.",
      "read CHANGELOG.md",
      "Checking the tags now.",
      "bash git tag",
    ]),
  );
});

// The step and the terminal frame land in renders of their own, which is what a turn does over the
// wire: the fold is written open while the turn runs, and the browser answers that write with a
// `toggle` event of its own. A test that emits both frames in one batch renders once and never
// opens the fold, so it cannot see what that event leaves behind.
test("the fold the running turn stood open folds when the turn settles", async () => {
  const stream = await streaming();
  stream.emit("tool", { tool: "bash", preview: "ls" });
  const fold = (await waitFor(() => document.querySelector("details")!)) as HTMLDetailsElement;
  await waitFor(() => expect(fold.open).toBe(true));

  stream.emit("terminal", {
    status: "done",
    text: "Done.",
    model: "opus",
    tokens: 5,
    cost_micro_usd: 1_000_000,
  });

  expect(await screen.findByText("Completed 1 step")).toBeTruthy();
  await waitFor(() => expect(document.querySelector("details")!.open).toBe(false));
  expect(screen.queryByText("bash ls")).toBeNull();
});

// The member's own toggle is the one state that outlives the default, so the frames a running turn
// keeps emitting must not open the fold again behind the member who folded it away.
test("a member who folds a running turn away keeps it away over the frames after it", async () => {
  const stream = await streaming();
  stream.emit("tool", { tool: "read", preview: "CHANGELOG.md" });

  const fold = document.querySelector("details") as HTMLDetailsElement;
  await waitFor(() => expect(fold.open).toBe(true));
  await delivered();

  await userEvent.click(fold.querySelector("summary")!);
  await delivered();
  expect(fold.open).toBe(false);

  stream.emit("skill", { skill: "calendar-triage" });
  expect(await screen.findByText("Loading skill · calendar-triage")).toBeTruthy();
  await delivered();
  expect(fold.open).toBe(false);
});

test("a thought a skill load interrupted stays a step when the turn is stopped", async () => {
  const stream = await streaming();
  stream.emit("message", { text: "Loading the triage skill." });
  stream.emit("skill", { skill: "calendar-triage" });
  stream.emit("terminal", { status: "cancelled" });

  const summary = await screen.findByText("Completed 2 steps");
  expect(screen.getByText("Stopped.")).toBeTruthy();

  await userEvent.click(summary);
  expect(screen.getByText("Loading the triage skill.")).toBeTruthy();
  expect(screen.getByText("Loaded skill · calendar-triage")).toBeTruthy();
});

test("a thought a subagent's dispatch interrupted stands ahead of the run's row", async () => {
  const stream = await streaming();
  stream.emit("message", { text: "Handing the research off." });
  stream.emit("tool", { tool: "spawn_subagent", preview: "general_purpose" });
  stream.emit("subagent_activity", {
    turn_id: "88888888-8888-4888-8888-888888888888",
    parent_turn_id: TURN_ID,
    conversation_id: "66666666-6666-4666-8666-666666666666",
    profile: "general_purpose",
    name: "",
    tool: "",
    description: "",
    preview: "",
    skill: "",
    status: "done",
  });
  stream.emit("terminal", {
    status: "done",
    text: "It shipped Tuesday.",
    model: "opus",
    tokens: 5,
    cost_micro_usd: 1_000_000,
  });

  const summary = await screen.findByText("Completed 3 steps");
  await userEvent.click(summary);
  const log = document.body.textContent ?? "";
  expect(log.indexOf("Handing the research off.")).toBeLessThan(
    log.indexOf("spawn_subagent general_purpose"),
  );
  expect(log.indexOf("spawn_subagent general_purpose")).toBeLessThan(
    log.indexOf("Subagent · general_purpose"),
  );
});

test("a run's frame leaves the answer the turn has already streamed where it stands", async () => {
  const stream = await streaming();
  const answer = "The research runs on; I will say what it finds.";
  stream.emit("tool", { tool: "spawn_subagent", preview: "general_purpose" });
  stream.emit("message", { text: answer });
  expect(await screen.findByText(saying(answer))).toBeTruthy();

  // A background run publishes onto this stream while the parent writes its closing answer.
  stream.emit("subagent_activity", {
    turn_id: "88888888-8888-4888-8888-888888888888",
    parent_turn_id: TURN_ID,
    conversation_id: "66666666-6666-4666-8666-666666666666",
    profile: "general_purpose",
    name: "",
    tool: "",
    description: "",
    preview: "",
    skill: "",
    status: "",
  });
  expect(screen.getByText(saying(answer))).toBeTruthy();

  stream.emit("terminal", {
    status: "done",
    text: answer,
    model: "opus",
    tokens: 5,
    cost_micro_usd: 1_000_000,
  });

  const summary = await screen.findByText("Completed 2 steps");
  await userEvent.click(summary);
  const rows = summary.closest("details")!.querySelectorAll(":scope > ul > li");
  expect([...rows].map((row) => row.textContent)).toEqual([
    "spawn_subagent general_purpose",
    "Subagent · general_purpose",
  ]);
  expect(screen.getByText(saying(answer))).toBeTruthy();
});

test("a thought flushed to a step survives the drain that ends its round", async () => {
  const stream = await streaming();
  stream.emit("message", { text: "Reading the changelog first." });
  stream.emit("tool", { tool: "read", preview: "CHANGELOG.md" });
  stream.emit("absorbed", { arrivals: [ARRIVAL_ID] });
  stream.emit("message", { text: "It shipped Tuesday." });
  stream.emit("terminal", {
    status: "done",
    text: "It shipped Tuesday.",
    model: "opus",
    tokens: 5,
    cost_micro_usd: 1_000_000,
  });

  // The round the drain closed wrote a thought and dispatched a call. Both are the work behind the
  // reply it settled as, and a drain is not a reason to lose them.
  const summary = await screen.findByText("Completed 2 steps");
  await userEvent.click(summary);
  const rows = summary.closest("details")!.querySelectorAll("li");
  expect([...rows].map((row) => row.textContent)).toEqual([
    "Reading the changelog first.",
    "read CHANGELOG.md",
  ]);
  expect(screen.getByText("It shipped Tuesday.")).toBeTruthy();
});

test("a reloaded turn draws the thought it settled into as a step", async () => {
  location.hash = "#/c/" + CONVO_ID;
  wire({
    "/api/chats": () => json({ chats: [CHAT_ROW] }),
    "/transcript": () =>
      json({
        messages: [
          { role: "user", text: "What shipped?" },
          {
            role: "assistant",
            text: "It shipped Tuesday.",
            events: [{ kind: "note", text: "Reading the changelog first." }],
          },
        ],
      }),
    "/slots": () => json({ slots: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const summary = await screen.findByText("Completed 1 step");
  expect(screen.queryByText("Reading the changelog first.")).toBeNull();

  await userEvent.click(summary);
  expect(screen.getByText("Reading the changelog first.")).toBeTruthy();
  expect(screen.getByText("It shipped Tuesday.")).toBeTruthy();
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

  const summary = await screen.findByText("Completed 1 step");
  expect(summary.closest("summary")).toBeTruthy();
  expect(screen.queryByText("Subagent · general_purpose")).toBeNull();

  await userEvent.click(summary);
  const card = screen.getByText("Subagent · general_purpose");
  expect(card.closest("a")).toBeNull();
  expect(screen.queryByText("Reading the changelog.")).toBeNull();

  await userEvent.click(card.closest("summary")!);
  expect(screen.getByText("Reading the changelog.")).toBeTruthy();
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

test("an apps frame draws the application the turn created, pressable to its own screen", async () => {
  const stream = await streaming();
  stream.emit("apps", {
    apps: [{ id: SECOND_ID, name: "daily-digest", model: "claude-sonnet-5", icon: "notebook" }],
  });
  const card = await screen.findByRole("link", { name: /Daily-Digest/ });
  expect(card.getAttribute("href")).toBe("#/agents/" + SECOND_ID);
  expect(card.textContent).toContain("claude-sonnet-5");

  stream.emit("terminal", {
    status: "done",
    text: "daily-digest is set up.",
    model: "opus",
    tokens: 4,
    cost_micro_usd: 1_000_000,
  });
  await waitFor(() =>
    expect(screen.getAllByRole("link", { name: /Daily-Digest/ })).toHaveLength(1),
  );
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

test("an arrival the reload found undrained waits until an absorbed frame names it", async () => {
  location.hash = "#/c/" + CONVO_ID;
  wire({
    "/api/chats": () => json({ chats: [CHAT_ROW] }),
    "/transcript": () =>
      json({
        messages: [
          { role: "user", text: "Review PR 1268." },
          { role: "user", text: "and the tests", arrival_id: ARRIVAL_ID },
        ],
        turn: TURN_ID,
      }),
    "/slots": () => json({ slots: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await waitFor(() =>
    expect(screen.getByText("and the tests").classList.contains("italic")).toBe(true),
  );
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));

  StreamFake.last().emit("absorbed", { arrivals: [ARRIVAL_ID] });
  await waitFor(() =>
    expect(screen.getByText("and the tests").classList.contains("italic")).toBe(false),
  );
});

test("a terminal that is not done states the status and error class it carries", async () => {
  const stream = await streaming();
  stream.emit("terminal", { status: "failed", error_class: "ProviderTimeout" });
  expect(await screen.findByText("(failed: ProviderTimeout)")).toBeTruthy();
  expect(stream.closed).toBe(true);
});

test("a cancelled turn reads as the member's own word for it", async () => {
  const stream = await streaming();
  stream.emit("terminal", { status: "cancelled" });
  expect(await screen.findByText("Stopped.")).toBeTruthy();
});

test("a terminal that is not done and names no error class states the status alone", async () => {
  const stream = await streaming();
  stream.emit("terminal", { status: "refused" });
  expect(await screen.findByText("(refused)")).toBeTruthy();
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

test("a single-choice question holds one option at a time", async () => {
  await asked(ENTRY);
  const work = (await screen.findByRole("radio", { name: "Work" })) as HTMLInputElement;
  const home = screen.getByRole("radio", { name: "Home" }) as HTMLInputElement;
  await userEvent.click(work);
  await userEvent.click(home);
  expect(work.checked).toBe(false);
  expect(home.checked).toBe(true);
});

test("a multi-select question offers a toggle per option", async () => {
  await asked({ ...ENTRY, multi_select: true });
  const work = (await screen.findByRole("checkbox", { name: "Work" })) as HTMLInputElement;
  expect(work.checked).toBe(false);
  await userEvent.click(work);
  expect(work.checked).toBe(true);
  await userEvent.click(screen.getByRole("checkbox", { name: "Home" }));
  expect(work.checked).toBe(true);
});

test("a free-text-only question takes words rather than a choice", async () => {
  await asked({ ...ENTRY, free_text_only: true });
  expect(await screen.findByText("Which calendar?")).toBeTruthy();
  expect(screen.queryByRole("radio", { name: "Work" })).toBeNull();
  expect(screen.queryByRole("checkbox", { name: "Work" })).toBeNull();
  expect(screen.getByRole("textbox", { name: "Which calendar?" })).toBeTruthy();
});

test("a question allowing attachments is answered in the message box, not the form", async () => {
  await asked({ ...ENTRY, allow_attachments: true });
  expect(await screen.findByText("Which calendar?")).toBeTruthy();
  // The options are stated so the member knows what is on offer, but none is a control, and there
  // is no box to type in either: the answer carries a file, and only the composer takes one.
  expect(screen.queryByRole("radio", { name: "Work" })).toBeNull();
  expect(screen.queryByRole("checkbox", { name: "Work" })).toBeNull();
  expect(screen.queryByRole("textbox", { name: "Which calendar?" })).toBeNull();
  expect(screen.getByText(/answer in the message box below/i)).toBeTruthy();
});

test("a question with more options than fit offers none of them as buttons", async () => {
  const many = Array.from({ length: 11 }, (_, index) => ({ label: "option-" + index }));
  await asked({ question: "Which one?", options: many });
  expect(await screen.findByText("Which one?")).toBeTruthy();
  expect(screen.queryByRole("radio", { name: "option-0" })).toBeNull();
  expect(screen.queryByRole("checkbox", { name: "option-0" })).toBeNull();
  expect(screen.getByText(/answer in the message box below/i)).toBeTruthy();
});

test("the slot strip draws the option every pressed control in the portal is drawn as", async () => {
  location.hash = "#/c/" + CONVO_ID;
  wire({
    ["/conversations/" + CONVO_ID + "/slots/changes"]: () =>
      json({ type: "changes", changes: [], truncated: false }),
    "/api/chats": () => json({ chats: [CHAT_ROW] }),
    "/transcript": () => json({ messages: [] }),
    "/slots": () =>
      json({
        slots: [
          { id: "changes", label: "Changes", icon: "diff", kind: "changes", count: 2 },
          { id: "sources", label: "Sources", icon: "link", kind: "sources", count: 1 },
        ],
      }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const changes = await screen.findByRole("button", { name: "Changes 2" });
  // The rule is that a pressed control is drawn as pressed, which these four classes are. The
  // variant's whole class string is not asserted: the strip stands in a header bar, so it also
  // takes `size="bar"`, and the height and the pill shape override the variant's own padding and
  // radius — a verbatim match would be asserting that no control may ever be sized.
  expect(changes.className).toContain("aria-pressed:bg-ink");
  expect(changes.className).toContain("aria-pressed:text-surface");
  expect(changes.className).toContain("aria-pressed:border-ink");
  expect(changes.className).toContain("hover:bg-fill");
  expect(changes.getAttribute("aria-pressed")).toBe("false");

  await userEvent.click(changes);
  expect(changes.getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByRole("button", { name: "Sources 1" }).getAttribute("aria-pressed")).toBe(
    "false",
  );
});

test("a send whose answer is not json ends the wait instead of disabling the composer", async () => {
  wire({
    "/transcript": () => json({ messages: [] }),
    "/chat": () => new Response("<html>", { status: 200 }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
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

  await userEvent.click(await screen.findByRole("radio", { name: "Work" }));
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  expect(await screen.findByText("Network error — try again.")).toBeTruthy();
});

test("an answer the server refuses states its status rather than hanging", async () => {
  await asked(ENTRY);
  wire({
    "/transcript": () => json({ messages: [] }),
    "/chat": () => new Response("nope", { status: 503 }),
  });

  await userEvent.click(await screen.findByRole("radio", { name: "Work" }));
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  expect(await screen.findByText("Error 503 — try again.")).toBeTruthy();
});

test("token counts group their thousands", () => {
  expect(tokens(12)).toBe("12");
  expect(tokens(66_473)).toBe("66,473");
});
