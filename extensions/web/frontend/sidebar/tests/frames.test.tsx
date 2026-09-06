import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { tokens } from "@/lib/turnStream";

import { AGENT, ARRIVAL_ID, CHAT_ROW, chatsOnWire, CONVO_ID, json, MEMBER, saying, SECOND_ID, StreamFake, TURN_ID, useStreamFake, wire } from "./harness";

async function streaming() {
  wire({
    "/transcript": () => json({ messages: [] }),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "go" }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await userEvent.type(screen.getByLabelText("Ask UFO"), "go");
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

test("an activity frame decodes its complete description", async () => {
  const stream = await streaming();
  const label = "Reading the calendar."
  stream.emit("activity", { text: label });
  // The line reads the step out to a screen reader, and draws it as the cipher decoding into it.
  const dock = await screen.findByText(label, { selector: ".sr-only" });
  const decoded = dock.parentElement!.querySelector("[data-slot=decode-text]")!;
  expect(decoded.textContent).toHaveLength(label.length);
  expect(decoded.textContent).not.toBe(label);
  expect(decoded.textContent?.replaceAll(" ", "")).not.toMatch(/[A-Za-z]/);
  // A space is a break between words rather than a cell, so it is the only character without one.
  expect(decoded.querySelectorAll("[data-slot=decode-cell]")).toHaveLength(
    label.replaceAll(" ", "").length,
  );
  expect(decoded.querySelector("[data-slot=decode-cell]")?.className).toContain(
    "w-(--size-decode-cell)",
  );
  // A cell is one character wide, so the cipher is set in the face that gives every glyph that
  // width rather than in the proportional face the line around it reads in.
  expect(decoded.className).toContain("font-mono");

  stream.emit("activity", { text: "Listing the workspace." });
  expect(await screen.findByText("Listing the workspace.", { selector: ".sr-only" })).toBeTruthy();
});

test("an activity frame names the guidance being loaded", async () => {
  const stream = await streaming();
  stream.emit("activity", { text: "Loading calendar guidance." });
  expect(
    await screen.findByText("Loading calendar guidance.", { selector: ".sr-only" }),
  ).toBeTruthy();
});

test("a settled turn states its latest call and opens onto the ones before it", async () => {
  const stream = await streaming();
  stream.emit("activity", { text: "Listing the workspace." });
  stream.emit("activity", { text: "Reading the notes." });
  stream.emit("activity", { text: "Loading calendar guidance." });
  stream.emit("terminal", {
    status: "done",
    text: "Looked it over.",
    model: "opus",
    tokens: 5,
    cost_micro_usd: 1_000_000,
  });

  const summary = await screen.findByText("Completed 3 steps");
  expect(summary.closest("summary")).toBeTruthy();
  expect(screen.queryByText("Listing the workspace.")).toBeNull();
  expect(screen.queryByText("Loading calendar guidance.")).toBeNull();

  await userEvent.click(summary);
  expect(screen.getByText("Listing the workspace.").closest("details")).toBe(
    summary.closest("details"),
  );
  expect(screen.getByText("Reading the notes.")).toBeTruthy();
  expect(screen.getByText("Loading calendar guidance.")).toBeTruthy();
});

test("one tool call states itself, with nothing more behind it", async () => {
  const stream = await streaming();
  stream.emit("activity", { text: "Listing the workspace." });
  stream.emit("terminal", {
    status: "done",
    text: "Done.",
    model: "opus",
    tokens: 5,
    cost_micro_usd: 1_000_000,
  });
  const summary = await screen.findByText("Completed 1 step");
  expect(summary.closest("summary")).toBeTruthy();
  expect(screen.queryByText("Listing the workspace.")).toBeNull();

  await userEvent.click(summary);
  expect(screen.getByText("Listing the workspace.")).toBeTruthy();
});

test("a late activity frame leaves the reply already streaming where it stands", async () => {
  const stream = await streaming();
  const answer = "It shipped Tuesday.";
  stream.emit("message", { text: answer });
  expect(await screen.findByText(saying(answer))).toBeTruthy();

  stream.emit("activity", { text: "Reading the changelog." });
  expect(screen.getByText(saying(answer))).toBeTruthy();
  stream.emit("terminal", {
    status: "done",
    text: answer,
    model: "opus",
    tokens: 5,
    cost_micro_usd: 1_000_000,
  });

  const summary = await screen.findByText("Completed 1 step");
  expect(screen.getByText(answer)).toBeTruthy();

  await userEvent.click(summary);
  const rows = summary.closest("details")!.querySelectorAll("li");
  expect([...rows].map((row) => row.textContent)).toEqual(["Reading the changelog."]);
});

test("the fold a running turn writes into stays closed, and takes its steps once opened", async () => {
  const stream = await streaming();
  stream.emit("message", { text: "Reading the changelog first." });
  stream.emit("activity", { text: "Reading the changelog." });
  expect(await screen.findByText(saying("Reading the changelog first."))).toBeTruthy();

  const fold = document.querySelector("details") as HTMLDetailsElement;
  expect(fold.open).toBe(false);
  expect(fold.querySelectorAll("li")).toHaveLength(0);

  await userEvent.click(fold.querySelector("summary")!);
  await delivered();
  expect(fold.open).toBe(true);

  stream.emit("message", { text: "Checking the tags now." });
  stream.emit("activity", { text: "Checking the release tags." });
  await waitFor(() =>
    expect([...fold.querySelectorAll("li")].map((row) => row.textContent)).toEqual([
      "Reading the changelog.",
      "Checking the release tags.",
    ]),
  );
});

test("the fold stays closed through the turn settling", async () => {
  const stream = await streaming();
  stream.emit("activity", { text: "Listing the workspace." });
  const fold = (await waitFor(() => document.querySelector("details")!)) as HTMLDetailsElement;
  expect(fold.open).toBe(false);
  expect(fold.querySelectorAll("li")).toHaveLength(0);

  stream.emit("terminal", {
    status: "done",
    text: "Done.",
    model: "opus",
    tokens: 5,
    cost_micro_usd: 1_000_000,
  });

  expect(await screen.findByText("Completed 1 step")).toBeTruthy();
  await waitFor(() => expect(document.querySelector("details")!.open).toBe(false));
  expect(screen.queryByText("Listing the workspace.")).toBeNull();
});

// The member's own toggle is the one state there is, so the frames a running turn keeps emitting
// must not close the fold behind the member who opened it.
test("a member who opens a running turn keeps it open over the frames after it", async () => {
  const stream = await streaming();
  stream.emit("activity", { text: "Reading the changelog." });

  const fold = (await waitFor(() => document.querySelector("details")!)) as HTMLDetailsElement;
  expect(fold.open).toBe(false);
  await delivered();

  await userEvent.click(fold.querySelector("summary")!);
  await delivered();
  expect(fold.open).toBe(true);

  stream.emit("activity", { text: "Loading calendar guidance." });
  expect(await screen.findAllByText("Loading calendar guidance.")).toHaveLength(2);
  await delivered();
  expect(fold.open).toBe(true);
});

test("an activity stays a step when the turn is stopped", async () => {
  const stream = await streaming();
  stream.emit("message", { text: "Loading the triage skill." });
  stream.emit("activity", { text: "Loading calendar guidance." });
  stream.emit("terminal", { status: "cancelled" });

  const summary = await screen.findByText("Completed 1 step");
  expect(document.body.textContent).toContain("Stopped.");

  await userEvent.click(summary);
  expect(screen.getByText("Loading calendar guidance.")).toBeTruthy();
});

test("a subagent's dispatch stands ahead of the run's row", async () => {
  const stream = await streaming();
  stream.emit("message", { text: "Handing the research off." });
  stream.emit("activity", { text: "Delegating the research." });
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

  const summary = await screen.findByText("Completed 2 steps");
  await userEvent.click(summary);
  const log = document.body.textContent ?? "";
  expect(log.indexOf("Delegating the research.")).toBeLessThan(
    log.indexOf("Subagent · general_purpose"),
  );
});

test("a run's frame leaves the answer the turn has already streamed where it stands", async () => {
  const stream = await streaming();
  const answer = "The research runs on; I will say what it finds.";
  stream.emit("activity", { text: "Delegating the research." });
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
    "Delegating the research.",
    "Subagent · general_purpose",
  ]);
  expect(screen.getByText(saying(answer))).toBeTruthy();
});

test("an activity survives the drain that ends its round", async () => {
  const stream = await streaming();
  stream.emit("message", { text: "Reading the changelog first." });
  stream.emit("activity", { text: "Reading the changelog." });
  stream.emit("absorbed", { arrivals: [ARRIVAL_ID] });
  stream.emit("message", { text: "It shipped Tuesday." });
  stream.emit("terminal", {
    status: "done",
    text: "It shipped Tuesday.",
    model: "opus",
    tokens: 5,
    cost_micro_usd: 1_000_000,
  });

  const summary = await screen.findByText("Completed 1 step");
  await userEvent.click(summary);
  const rows = summary.closest("details")!.querySelectorAll("li");
  expect([...rows].map((row) => row.textContent)).toEqual(["Reading the changelog."]);
  expect(screen.getByText("It shipped Tuesday.")).toBeTruthy();
});

test("a reloaded turn draws the thought it settled into as a step", async () => {
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

test("a connect frame offers the act, pressable to the address that mints its consent", async () => {
  const stream = await streaming();
  stream.emit("connect", { provider: "gmail", label: "Gmail", turn: TURN_ID });
  const link = await screen.findByRole("link", { name: "Connect Gmail" });
  // Nothing on the page holds a consent URL: the press lands here, and this mints one and
  // redirects, so what the member reaches is as fresh as their press.
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

  // Pressing the link is itself what re-reads the transcript: the consent window takes the focus
  // and gives it back, and the re-read serves the same open handoff the stream drew.
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

  // The reply was cut into the steps, so the terminal states no words — the control and the steps
  // are all the turn left, and they are what the member acts on.
  stream.emit("terminal", { status: "done", text: "", model: "opus", tokens: 5, cost_micro_usd: 1 });
  await delivered();

  const link = screen.getByRole("link", { name: "Connect Gmail" });
  expect(link.getAttribute("href")).toBe("/surface/web/turns/" + TURN_ID + "/connect");
  expect(screen.getByText("Completed 1 step")).toBeTruthy();
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
  // A browser closes a window a script opened, and only that. Sized for a consent screen, so the
  // conversation stays in sight behind it.
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

  // Nothing was opened for the member, so the anchor keeps its own way of getting them there.
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
  // The strip stands with the acts at the far end of the conversation's band, so each slot wears
  // the one act style that band takes: a quiet glyph in the control's own circle. The label and the
  // count are the act's accessible name rather than words beside the glyph, because a strip that
  // spelled every slot out would take the whole line the title is on.
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
  await userEvent.type(screen.getByLabelText("Ask UFO"), "go");
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
