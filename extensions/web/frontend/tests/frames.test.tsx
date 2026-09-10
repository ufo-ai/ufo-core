import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { tokens } from "@/lib/turnMeta";

import { AGENT, ARRIVAL_ID, CHAT_ROW, chatsOnWire, CONVO_ID, json, MEMBER, saying, SECOND_ID, StreamFake, TURN_ID, useStreamFake, wire } from "./harness";

const START_MS = 1_700_000_000_000;

async function streaming() {
  wire({
    "/transcript": () => json({ messages: [] }),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "go" }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await userEvent.type(await screen.findByLabelText("Ask UFO"), "go");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  return StreamFake.last();
}

/** A browser queues the `toggle` for an `open` a render wrote in a task of its own; a timer of the same
 *  kind lets it land before the next frame. */
const delivered = () =>
  act(async () => void (await new Promise((resolve) => setTimeout(resolve, 0))));

beforeEach(() => {
  useStreamFake();
});

/** The one line a running turn draws, and the step it currently states. */
const stepLine = () => document.querySelector<HTMLElement>("[data-slot=marker-content].shimmer");

test("an activity frame states its complete description on the line", async () => {
  const stream = await streaming();
  const label = "Reading the calendar.";
  stream.emit("activity", { text: label });
  const said = await screen.findByText(label);
  expect(said.className).toContain("sr-only");

  const line = said.closest("[data-slot=marker-content]")!;
  expect(line.className).toContain("shimmer");
  expect(line.closest("[data-slot=marker]")).toBeTruthy();
  const decoded = line.querySelector("[data-slot=decode-text]")!;
  expect(decoded.querySelectorAll("[data-slot=decode-cell]")).toHaveLength(
    label.replaceAll(" ", "").length,
  );

  stream.emit("activity", { text: "Listing the workspace." });
  expect(await screen.findByText("Listing the workspace.")).toBeTruthy();
  expect(screen.queryByText(label)).toBeNull();
});

test("an activity frame names the guidance being loaded", async () => {
  const stream = await streaming();
  stream.emit("activity", { text: "Loading calendar guidance." });
  const said = await screen.findByText("Loading calendar guidance.");
  expect(said.closest("[data-slot=marker-content]")!.className).toContain("shimmer");
});

test("a settled turn drops the line and states none of the steps it took", async () => {
  const stream = await streaming();
  const steps = ["Listing the workspace.", "Reading the notes.", "Loading calendar guidance."];
  for (const step of steps) stream.emit("activity", { text: step });
  expect(await screen.findByText(steps[2])).toBeTruthy();

  stream.emit("terminal", {
    status: "done",
    text: "Looked it over.",
    model: "opus",
    tokens: 5,
    cost_micro_usd: 1_000_000,
  });

  expect(await screen.findByText("Looked it over.")).toBeTruthy();
  await waitFor(() => expect(stepLine()).toBeNull());
  for (const step of steps) expect(screen.queryByText(step)).toBeNull();
});

test("a late activity frame leaves the reply already streaming where it stands", async () => {
  const stream = await streaming();
  const answer = "It shipped Tuesday.";
  stream.emit("message", { text: answer });
  const streamed = await screen.findByText(saying(answer));
  expect(streamed.querySelectorAll("[data-arrive]").length).toBeGreaterThan(0);
  expect(streamed.querySelector("[data-glyph]")).toBeNull();

  stream.emit("activity", { text: "Reading the changelog." });
  expect(await screen.findByText("Reading the changelog.")).toBeTruthy();
  expect(screen.getByText(saying(answer))).toBeTruthy();

  stream.emit("terminal", {
    status: "done",
    text: answer,
    model: "opus",
    tokens: 5,
    cost_micro_usd: 1_000_000,
  });

  expect(await screen.findByText(answer)).toBeTruthy();
  await waitFor(() => expect(stepLine()).toBeNull());
  expect(screen.queryByText("Reading the changelog.")).toBeNull();
});

test("a running turn states one step at a time, and holds no line for the ones before it", async () => {
  const stream = await streaming();
  stream.emit("message", { text: "Reading the changelog first." });
  stream.emit("activity", { text: "Reading the changelog." });
  expect(await screen.findByText(saying("Reading the changelog first."))).toBeTruthy();
  expect(await screen.findByText("Reading the changelog.")).toBeTruthy();

  stream.emit("message", { text: "Checking the tags now." });
  stream.emit("activity", { text: "Checking the release tags." });

  expect(await screen.findByText("Checking the release tags.")).toBeTruthy();
  expect(screen.queryByText("Reading the changelog.")).toBeNull();
  expect(document.querySelectorAll("[data-slot=marker-content].shimmer")).toHaveLength(1);
});

test("an activity leaves no line behind when the turn is stopped", async () => {
  const stream = await streaming();
  stream.emit("message", { text: "Loading the triage skill." });
  stream.emit("activity", { text: "Loading calendar guidance." });
  expect(await screen.findByText("Loading calendar guidance.")).toBeTruthy();

  stream.emit("terminal", { status: "cancelled" });

  await waitFor(() => expect(document.body.textContent).toContain("Stopped."));
  expect(stepLine()).toBeNull();
  expect(screen.queryByText("Loading calendar guidance.")).toBeNull();
});

test("a subagent still running is what the line states, in place of the step that dispatched it", async () => {
  const stream = await streaming();
  stream.emit("message", { text: "Handing the research off." });
  stream.emit("activity", { text: "Delegating the research." });
  expect(await screen.findByText("Delegating the research.")).toBeTruthy();

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

  expect(await screen.findByText("Awaiting 1 subagent")).toBeTruthy();
  expect(screen.queryByText("Delegating the research.")).toBeNull();
  expect(screen.queryByText("Subagent · general_purpose")).toBeNull();
});

test("a run's frame leaves the answer the turn has already streamed where it stands", async () => {
  const stream = await streaming();
  const answer = "The research runs on; I will say what it finds.";
  stream.emit("activity", { text: "Delegating the research." });
  stream.emit("message", { text: answer });
  expect(await screen.findByText(saying(answer))).toBeTruthy();

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
  expect(await screen.findByText("Awaiting 1 subagent")).toBeTruthy();

  stream.emit("terminal", {
    status: "done",
    text: answer,
    model: "opus",
    tokens: 5,
    cost_micro_usd: 1_000_000,
  });

  expect(await screen.findByText(answer)).toBeTruthy();
  await waitFor(() => expect(stepLine()).toBeNull());
});

test("an activity survives the drain that ends its round", async () => {
  const stream = await streaming();
  stream.emit("message", { text: "Reading the changelog first." });
  stream.emit("activity", { text: "Reading the changelog." });
  expect(await screen.findByText("Reading the changelog.")).toBeTruthy();

  stream.emit("absorbed", { arrivals: [ARRIVAL_ID] });
  stream.emit("message", { text: "It shipped Tuesday." });
  expect(await screen.findByText("Reading the changelog.")).toBeTruthy();

  stream.emit("terminal", {
    status: "done",
    text: "It shipped Tuesday.",
    model: "opus",
    tokens: 5,
    cost_micro_usd: 1_000_000,
  });

  expect(await screen.findByText("It shipped Tuesday.")).toBeTruthy();
  await waitFor(() => expect(stepLine()).toBeNull());
});

test("a reloaded turn draws the reply alone, and none of the thoughts behind it", async () => {
  location.hash = "#/c/" + CONVO_ID;
  wire({
    ...chatsOnWire([CHAT_ROW]),
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

  expect(await screen.findByText("It shipped Tuesday.")).toBeTruthy();
  expect(screen.queryByText("Reading the changelog first.")).toBeNull();
  expect(stepLine()).toBeNull();
});

test("a terminal subagent event keeps the work it did out of the transcript", async () => {
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

  expect(await screen.findByText("Done.")).toBeTruthy();
  expect(screen.queryByText("Subagent · general_purpose")).toBeNull();
  expect(screen.queryByText("Reading the changelog.")).toBeNull();
  expect(screen.queryByText("It shipped Tuesday.")).toBeNull();
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

test("a cost frame prices nothing on the screen while the turn is still running", async () => {
  const clock = vi.spyOn(Date, "now").mockReturnValue(START_MS);
  const stream = await streaming();
  stream.emit("activity", { text: "Reading the changelog." });
  clock.mockReturnValue(START_MS + 102_000);
  stream.emit("cost", { tokens: 29_000, cost_micro_usd: 5_000 });

  const line = await screen.findByText("Reading the changelog.");
  expect(line.closest("[data-slot=marker]")!.textContent).not.toContain("29K");
  expect(screen.getByTestId("log").textContent).not.toContain("29K tok");
  expect(screen.getByTestId("log").textContent).not.toContain("1m 42s");
  clock.mockRestore();
});

test("a settled turn states its spend beside the mark of the model that spent it", async () => {
  const clock = vi.spyOn(Date, "now").mockReturnValue(START_MS);
  const stream = await streaming();
  clock.mockReturnValue(START_MS + 102_000);
  stream.emit("cost", { tokens: 29_000, cost_micro_usd: 5_000 });
  stream.emit("terminal", {
    status: "done",
    text: "Looked it over.",
    model: "gpt-6-astra",
    tokens: 29_000,
    cost_micro_usd: 5_000,
  });

  const spend = await screen.findByText("29K tok");
  const line = spend.closest("[data-slot=marker]")!;
  expect(screen.getByText("<$0.01").closest("[data-slot=marker]")).toBe(line);
  expect(line.querySelector('[style*="--brand-openai"]')).toBeTruthy();
  expect(line.textContent).not.toContain("1m 42s");
  expect(screen.getByTestId("log").querySelector("time")).toBeNull();
  clock.mockRestore();
});

test("a connect frame offers the act, pressable to the address that mints its consent", async () => {
  const stream = await streaming();
  stream.emit("connect", { provider: "gmail", label: "Gmail", turn: TURN_ID });
  const link = await screen.findByRole("link", { name: "Connect Gmail" });
  expect(link.getAttribute("href")).toBe("/surface/web/turns/" + TURN_ID + "/connect");
});

test("the consent link stands after the page re-reads the transcript", async () => {
  const stream = await streaming();
  stream.emit("connect", { provider: "gmail", label: "Gmail", turn: TURN_ID });
  await screen.findByRole("link", { name: "Connect Gmail" });
  stream.emit("terminal", {
    status: "done",
    text: "Authorize it.",
    model: "opus",
    tokens: 5,
    cost_micro_usd: 1,
  });
  await screen.findByText("Authorize it.");

  wire({
    "/transcript": () =>
      json({
        messages: [
          { role: "user", text: "go" },
          {
            role: "assistant",
            text: "Authorize it, says the record.",
            connect: { provider: "gmail", label: "Gmail", turn: TURN_ID },
          },
        ],
      }),
  });
  act(() => {
    window.dispatchEvent(new Event("focus"));
  });

  await screen.findByText("Authorize it, says the record.");
  const link = await screen.findByRole("link", { name: "Connect Gmail" });
  expect(link.getAttribute("href")).toBe("/surface/web/turns/" + TURN_ID + "/connect");
});

test("a connect turn that ends wordless keeps the control it posted", async () => {
  const stream = await streaming();
  stream.emit("activity", { text: "Connecting Gmail." });
  stream.emit("connect", { provider: "gmail", label: "Gmail", turn: TURN_ID });
  await screen.findByRole("link", { name: "Connect Gmail" });

  stream.emit("terminal", { status: "done", text: "", model: "opus", tokens: 5, cost_micro_usd: 1 });
  await delivered();

  const link = screen.getByRole("link", { name: "Connect Gmail" });
  expect(link.getAttribute("href")).toBe("/surface/web/turns/" + TURN_ID + "/connect");
  expect(stepLine()).toBeNull();
  expect(screen.queryByText("Connecting Gmail.")).toBeNull();
});

test("consent opens in a window this page owns, so its return page can close itself", async () => {
  const stream = await streaming();
  const consent = { focus: vi.fn() };
  const open = vi.spyOn(window, "open").mockReturnValue(consent as unknown as Window);
  stream.emit("connect", { provider: "gmail", label: "Gmail", turn: TURN_ID });
  const link = await screen.findByRole("link", { name: "Connect Gmail" });

  await userEvent.click(link);

  const [url, name, features] = open.mock.calls[0];
  expect(url).toBe("/surface/web/turns/" + TURN_ID + "/connect");
  expect(name).toBe("ufo-connect");
  // A browser closes a window a script opened, and only that.
  expect(features).toContain("popup");
  expect(features).toContain("width=520");
  expect(consent.focus).toHaveBeenCalled();
  open.mockRestore();
});

test("a blocked consent window falls through to the tab the link already opens", async () => {
  const stream = await streaming();
  const open = vi.spyOn(window, "open").mockReturnValue(null);
  stream.emit("connect", { turn: TURN_ID });
  const link = await screen.findByRole("link", { name: "Connect account" });
  const clicked = new MouseEvent("click", { bubbles: true, cancelable: true });

  act(() => {
    link.dispatchEvent(clicked);
  });

  expect(clicked.defaultPrevented).toBe(false);
  expect(link.getAttribute("target")).toBe("_blank");
  open.mockRestore();
});

test("an apps frame draws the application the turn created, pressable to its own screen", async () => {
  const stream = await streaming();
  stream.emit("apps", {
    apps: [{ id: SECOND_ID, name: "daily-digest", model: "claude-sonnet-5", icon: "acanthus" }],
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
    ...chatsOnWire([CHAT_ROW]),
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
    model: "claude-opus-5",
    tokens: 800,
    cost_micro_usd: 12000,
  });
  const meta = (await screen.findByText("800 tok")).closest("[data-slot=marker-content]")!;
  expect([...meta.children].map((part) => part.tagName)).toEqual(["SPAN", "SPAN", "SPAN"]);
  expect([...meta.children].slice(0, 2).map((part) => part.textContent)).toEqual([
    "800 tok",
    "$0.01",
  ]);
  expect(meta.children[2].textContent).toMatch(/^\d{1,2}:\d{2} (AM|PM)$/);
  expect(meta.textContent).not.toContain("·");
  expect(meta.className).not.toContain("font-mono");

  const line = meta.closest("[data-slot=marker]")!;
  const copy = line.querySelector('button[aria-label="Copy message"]')!;
  expect(copy.compareDocumentPosition(meta) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  const mark = line.querySelector<HTMLElement>('[style*="--brand-anthropic"]')!;
  expect(mark.parentElement!.querySelector(".sr-only")!.textContent).toBe("Opus 5");
  expect(await screen.findByText("Answered.")).toBeTruthy();
});

test("a member's own message states the moment it landed and nothing else", async () => {
  const stream = await streaming();
  stream.emit("terminal", {
    status: "done",
    text: "Answered.",
    model: "claude-opus-5",
    tokens: 800,
    cost_micro_usd: 12000,
  });
  await screen.findByText("Answered.");

  const said = screen.getByText("go").closest("[data-slot=message]")!;
  const meta = said.querySelector("[data-slot=marker-content]")!;
  expect([...meta.children].map((part) => part.textContent)).toEqual([
    expect.stringMatching(/^\d{1,2}:\d{2} (AM|PM)$/),
  ]);
  expect(said.querySelector('button[aria-label="Copy message"]')).toBeTruthy();
  expect(meta.closest("[data-slot=marker]")!.className).toContain("opacity-0");
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

test("the slot strip is one quiet act per slot, the open one drawn as held", async () => {
  location.hash = "#/c/" + CONVO_ID;
  wire({
    ["/conversations/" + CONVO_ID + "/slots/changes"]: () =>
      json({ type: "changes", changes: [], truncated: false }),
    ...chatsOnWire([CHAT_ROW]),
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
  expect(changes.className).toContain("border-transparent");
  expect(changes.className).toContain("bg-transparent");
  expect(changes.className).toContain("hover:bg-fill");
  expect(changes.className).toContain("size-(--size-control)");
  expect(changes.textContent).toBe("");
  expect(changes.getAttribute("aria-pressed")).toBe("false");

  await userEvent.click(changes);
  expect(changes.getAttribute("aria-pressed")).toBe("true");
  expect(changes.className.split(" ")).toContain("bg-fill");
  const sources = screen.getByRole("button", { name: "Sources 1" });
  expect(sources.getAttribute("aria-pressed")).toBe("false");
  expect(sources.className.split(" ")).not.toContain("bg-fill");
});

test("a send whose answer is not json ends the wait instead of disabling the composer", async () => {
  wire({
    "/transcript": () => json({ messages: [] }),
    "/chat": () => new Response("<html>", { status: 200 }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await userEvent.type(await screen.findByLabelText("Ask UFO"), "go");
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

test("token counts read short enough to re-read every second", () => {
  expect(tokens(12)).toBe("12");
  expect(tokens(29_000)).toBe("29K");
  expect(tokens(66_473)).toBe("66.5K");
  expect(tokens(1_450_000)).toBe("1.5M");
});
