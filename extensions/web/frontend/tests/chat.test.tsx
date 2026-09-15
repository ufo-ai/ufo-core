import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { wakeAppStatus } from "@/lib/appStatusStore";
import { liveTurn, resetChatStore } from "@/lib/chatStore";
import { MessageLog, TranscriptScroll } from "@/kernel/messages";
import { ADMIN_DISCLOSURE } from "@/lib/audience";
import { resetStreams } from "@/lib/turnStream";
import type { EarlierMessages } from "@/lib/earlier";
import { chatHash, conversationSlotHash, newChatHash, workspaceHash } from "@/lib/route";
import { Chat } from "@/views/Chat";
import { ConversationTranscript } from "@/views/Conversations";

import { AGENT, AGENT_ID, ARRIVAL_ID, atPhoneWidth, audienceMark, CHAT_APP, CHAT_APP_ID, CHAT_ROW, chatsOnWire, CONVO_ID, json, MEMBER, saying, SECOND, SECOND_ID, StreamFake, TURN_ID, type Route, useStreamFake, wire } from "./harness";

beforeEach(() => {
  location.hash = "#/c/" + CONVO_ID;
  useStreamFake();
});

/** `laidLog` measures the log through `Element.prototype`, which every later test here shares. Undoing
 *  it now rather than at the end means one failed assertion takes down that test alone. */
afterEach(() => {
  vi.restoreAllMocks();
});

const transcript = (payload: unknown = { messages: [] }) => ({
  ...chatsOnWire([CHAT_ROW]),
  "/transcript": () => json(payload),
  "/slots": () =>
    json({
      slots: [
        { id: "changes", label: "Changes", icon: "diff", kind: "changes", count: 0 },
      ],
    }),
});

function open() {
  return render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
}

function waiting(text: string): boolean {
  return screen.getByText(text).classList.contains("italic");
}

function follow(hash: string): void {
  location.hash = hash;
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}

function Layered({ composing }: { composing: boolean }) {
  return (
    <>
      <div role="dialog">
        <input aria-label="Search" />
      </div>
      {composing ? (
        <Chat agent={AGENT} member={MEMBER} conversationId={CONVO_ID} focusComposer />
      ) : null}
    </>
  );
}

test("the composer takes focus where it stands, and leaves the focus an open layer holds", async () => {
  wire(transcript());
  render(<Chat agent={AGENT} member={MEMBER} conversationId={CONVO_ID} focusComposer />);

  expect(await screen.findByLabelText("Ask UFO")).toBe(document.activeElement);

  cleanup();
  const { rerender } = render(<Layered composing={false} />);
  const box = screen.getByLabelText("Search");
  box.focus();
  rerender(<Layered composing />);

  expect(await screen.findByLabelText("Ask UFO")).toBeTruthy();
  expect(document.activeElement).toBe(box);
});

const REFUSED_TURN = "44444444-4444-4444-8444-444444444444";
const REPLY_ID = "66666666-6666-4666-8666-666666666666";

const FOLDED = { turn_id: TURN_ID, conversation_id: CONVO_ID, title: "go", opened_run: false };

async function sendingMidTurn(chat: Route): Promise<void> {
  wire({
    ...transcript({ messages: [{ role: "user", text: "Review PR 1268." }], turn: TURN_ID }),
    "/chat": chat,
  });
  open();

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  await userEvent.type(screen.getByLabelText("Ask UFO"), "and again");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  expect(await screen.findByText("and again")).toBeTruthy();
}

test("an empty conversation states it, and the composer sends a message and streams the reply", async () => {
  wire({
    ...transcript(),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }),
  });
  open();

  expect(await screen.findByText("No messages in this conversation yet.")).toBeTruthy();

  await userEvent.type(screen.getByLabelText("Ask UFO"), "hello");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  expect(StreamFake.last().url).toBe("/surface/web/turns/" + TURN_ID + "/stream");
  expect(screen.getByText("hello")).toBeTruthy();

  StreamFake.last().emit("message", { text: "one " });
  StreamFake.last().emit("message", { text: "two" });
  expect(await screen.findByText(saying("one two"))).toBeTruthy();

  StreamFake.last().emit("terminal", {
    status: "done",
    model: "gpt-6-astra",
    tokens: 12,
    cost_micro_usd: 2_000_000,
  });
  const meta = (await screen.findByText("12 tok")).closest("[data-slot=marker]")!;
  expect(within(meta as HTMLElement).getByText("$2.00")).toBeTruthy();
  expect(meta.textContent).not.toContain("·");
  const mark = meta.querySelector<HTMLElement>('[style*="--brand-openai"]')!;
  expect(mark.getAttribute("class")).toContain("size-icon");
  fireEvent.focus(mark.parentElement!);
  expect((await screen.findByRole("tooltip")).textContent).toBe("GPT-6 Astra");
  expect(StreamFake.last().closed).toBe(true);
});

test("the open chat states who reads it on its title line, as the member's own control", async () => {
  wire(transcript());
  open();

  await screen.findByText("No messages in this conversation yet.");
  const detail = "Only you read this conversation. " + ADMIN_DISCLOSURE;
  const control = screen.getByRole("button", { name: "Visibility: Private" });
  expect(control.parentElement!.textContent).toContain(CHAT_ROW.title);
  expect(control.querySelector("svg")).toBeTruthy();
  expect(control.getAttribute("title")).toBe(detail);
  expect(document.body.querySelector("[data-slot=audience]")).toBeNull();
  fireEvent.focus(control);
  expect((await screen.findByRole("tooltip")).textContent).toBe("Only you");
});

test("a chat another member owns states its audience by that address", async () => {
  wire({
    ...transcript(),
    ...chatsOnWire([{ ...CHAT_ROW, audience: "member:m2", member_email: "mel@example.com" }]),
  });
  open();

  await screen.findByText("No messages in this conversation yet.");
  fireEvent.focus(audienceMark());
  expect((await screen.findByRole("tooltip")).textContent).toBe("Private to mel@example.com");
});

test("a reply from an agent on auto states its spend and names no model", async () => {
  wire({
    ...transcript(),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }),
  });
  render(<App agents={[{ ...AGENT, model: "auto" }]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByText("No messages in this conversation yet.");
  await userEvent.type(screen.getByLabelText("Ask UFO"), "hello");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("message", { text: "one two" });
  expect(await screen.findByText(saying("one two"))).toBeTruthy();

  StreamFake.last().emit("terminal", {
    status: "done",
    model: "glm-5.3-flash",
    tokens: 12,
    cost_micro_usd: 2_000_000,
  });
  const spend = (await screen.findByText("12 tok")).closest("[data-slot=marker]")!;
  expect(within(spend as HTMLElement).getByText("$2.00")).toBeTruthy();
  expect(spend.querySelector('[style*="--brand-"]')).toBeNull();
  expect(screen.queryByText(/GLM 5.3 Flash/)).toBeNull();
});

test("a settled reply states what it spent, with no stream to read it from", async () => {
  wire(
    transcript({
      messages: [
        { role: "user", text: "Review PR 1268." },
        {
          role: "assistant",
          text: "Reviewed.",
          events: [{ kind: "activity", text: "Reading the diff." }],
          summary: {
            model: "gpt-6-astra",
            tokens: 12,
            cost_micro_usd: 2_000_000,
            duration_ms: 63_000,
          },
        },
      ],
    }),
  );
  open();

  expect(await screen.findByText(saying("Reviewed."))).toBeTruthy();
  const meta = screen.getByText("12 tok").closest("[data-slot=marker]")!;
  expect(within(meta as HTMLElement).getByText("$2.00")).toBeTruthy();
  expect(meta.querySelector('[style*="--brand-openai"]')).toBeTruthy();
  expect(screen.queryByText("Reading the diff.")).toBeNull();
  expect(StreamFake.opened.length).toBe(0);
});

test("a settled reply names no model where the agent runs on the deploy's own choice", async () => {
  wire(
    transcript({
      messages: [
        { role: "user", text: "Review PR 1268." },
        {
          role: "assistant",
          text: "Reviewed.",
          summary: { tokens: 12, cost_micro_usd: 2_000_000, duration_ms: 63_000 },
        },
      ],
    }),
  );
  open();

  expect(await screen.findByText(saying("Reviewed."))).toBeTruthy();
  const meta = screen.getByText("12 tok").closest("[data-slot=marker]")!;
  expect(meta.querySelector('[style*="--brand-"]')).toBeNull();
});

test("the one control carries the act the member has, and stops the turn once", async () => {
  const { handler } = wire({
    ...transcript(),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }),
  });
  open();

  await screen.findByText("No messages in this conversation yet.");
  expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  await userEvent.type(screen.getByLabelText("Ask UFO"), "hello");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  expect(screen.getByRole("button", { name: "Stop" }).getAttribute("aria-disabled")).toBe(null);
  await userEvent.type(screen.getByLabelText("Ask UFO"), "and this too");
  expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  await userEvent.clear(screen.getByLabelText("Ask UFO"));

  await userEvent.click(screen.getByRole("button", { name: "Stop" }));
  const stops = () =>
    handler.mock.calls.filter(([, init]) => (init?.headers as Record<string, string>)?.["x-ufo-stop-turn"]);
  await waitFor(() => expect(stops()).toHaveLength(1));
  StreamFake.last().emit("terminal", { status: "cancelled" });
  await waitFor(() => expect(screen.queryByRole("button", { name: "Stop" })).toBeNull());
  expect(stops()).toHaveLength(1);
});

test("a reloaded conversation draws the reply alone, with no line for the steps behind it", async () => {
  wire(
    transcript({
      messages: [
        { role: "user", text: "inspect it" },
        {
          role: "assistant",
          text: "The tests pass.",
          events: [
            { kind: "activity", text: "Running the focused tests." },
            { kind: "activity", text: "Loading coding guidance." },
          ],
        },
      ],
    }),
  );
  open();

  expect(await screen.findByText(saying("The tests pass."))).toBeTruthy();
  expect(screen.queryByText("Running the focused tests.")).toBeNull();
  expect(screen.queryByText("Loading coding guidance.")).toBeNull();
  expect(document.querySelector("[data-slot=decode-text]")).toBeNull();
});

test("a bubble another member spoke names them, and the viewer's own carries no name", async () => {
  wire(
    transcript({
      messages: [
        { role: "user", text: "draft the tweets", speaker: "Sam Frost (peer@example.com)" },
        { role: "assistant", text: "Drafted." },
        { role: "user", text: "thanks" },
      ],
    }),
  );
  open();

  const named = (await screen.findByText("draft the tweets")).closest("[data-slot=message]");
  expect(named && within(named as HTMLElement).getByText("Sam Frost")).toBeTruthy();
  expect(screen.queryByText(/peer@example\.com/)).toBeNull();
  const own = screen.getByText("thanks").closest("[data-slot=message]");
  expect(own && within(own as HTMLElement).queryByText("Sam Frost")).toBeNull();
});

test("a bubble an object's fire admitted says ufo sent it", async () => {
  wire(
    transcript({
      messages: [
        { role: "user", text: "GitHub update: Fix the build", fired: { provider: "github" } },
        { role: "assistant", text: "Reviewed." },
        { role: "user", text: "thanks" },
      ],
    }),
  );
  open();

  const sent = (await screen.findByText("GitHub update: Fix the build")).closest(
    "[data-slot=message]",
  );
  expect(sent && within(sent as HTMLElement).getByText("Sent by UFO")).toBeTruthy();
  expect(sent?.querySelector("[style*='--brand-github']")).toBeTruthy();
  const own = screen.getByText("thanks").closest("[data-slot=message]");
  expect(own && within(own as HTMLElement).queryByText("Sent by UFO")).toBeNull();
});

test("a bubble marked markdown draws the emphasis, and one that is not draws the characters", async () => {
  wire(
    transcript({
      messages: [
        { role: "user", text: "**ship it** today", markdown: true },
        { role: "user", text: "**hold it** today" },
      ],
    }),
  );
  open();

  const spoken = (await screen.findByText("ship it")).closest("[data-slot=message]");
  expect(spoken?.querySelector("strong")?.textContent).toBe("ship it");
  const typed = screen.getByText("**hold it** today").closest("[data-slot=message]");
  expect(typed?.querySelector("strong")).toBeNull();
});

test("a bubble that answered a question draws the question over the words", async () => {
  wire(
    transcript({
      messages: [
        { role: "user", text: "Ship", asked: "Ship it?" },
        { role: "assistant", text: "Shipping." },
      ],
    }),
  );
  open();

  const bubble = (await screen.findByText("Ship")).closest("[data-slot=message]");
  expect(bubble && within(bubble as HTMLElement).getByText("Ship it?")).toBeTruthy();
});

test("a conversation reloaded while its turn runs shows the prompt, says so, and tails the turn", async () => {
  wire(transcript({ messages: [{ role: "user", text: "Review PR 1268." }], turn: TURN_ID }));
  open();

  expect(await screen.findByText("Review PR 1268.")).toBeTruthy();
  expect(await screen.findByText("Researching…")).toBeTruthy();
  expect(screen.queryByText("No messages in this conversation yet.")).toBeNull();
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  expect(StreamFake.last().url).toBe("/surface/web/turns/" + TURN_ID + "/stream");

  StreamFake.last().emit("activity", { text: "Reviewing the pull request." });
  expect(await screen.findAllByText("Reviewing the pull request.")).toHaveLength(1);
  expect(screen.queryByText("Researching…")).toBeNull();

  StreamFake.last().emit("terminal", {
    status: "done",
    text: "Reviewed it.",
    model: "opus",
    tokens: 9,
    cost_micro_usd: 1_000_000,
  });
  expect(await screen.findByText("Reviewed it.")).toBeTruthy();
  expect(StreamFake.last().closed).toBe(true);
});

test("the working line is not taken down when the turn's first step lands", async () => {
  wire(transcript({ messages: [{ role: "user", text: "Review PR 1268." }], turn: TURN_ID }));
  open();

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  const opening = await screen.findByText("Researching…");
  const waiting = opening.closest("[data-slot=marker]")!;
  expect(waiting.querySelector("[data-slot=marker-content]")!.classList).toContain("shimmer");
  const decoded = waiting.querySelector("[data-slot=decode-text]")!;
  expect(decoded.querySelectorAll("[data-slot=decode-cell]").length).toBeGreaterThan(0);

  StreamFake.last().emit("activity", { text: "Reviewing the pull request." });
  expect(await screen.findByText("Reviewing the pull request.")).toBe(opening);
  expect(opening.closest("[data-slot=marker]")).toBe(waiting);
});

test("a running turn draws its latest step alone, and takes the line down when it settles", async () => {
  wire(transcript({ messages: [{ role: "user", text: "Review PR 1268." }], turn: TURN_ID }));
  open();

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("activity", { text: "Reviewing the pull request." });
  StreamFake.last().emit("activity", { text: "Loading coding guidance." });

  expect(await screen.findByText("Loading coding guidance.")).toBeTruthy();
  expect(screen.queryByText("Reviewing the pull request.")).toBeNull();
  expect(document.querySelector("[data-slot=marker] [data-slot=decode-text]")).toBeTruthy();

  StreamFake.last().emit("terminal", {
    status: "done",
    text: "Reviewed it.",
    model: "opus",
    tokens: 9,
    cost_micro_usd: 1_000_000,
  });
  expect(await screen.findByText("Reviewed it.")).toBeTruthy();
  expect(screen.queryByText("Loading coding guidance.")).toBeNull();
  expect(document.querySelector("[data-slot=marker] [data-slot=decode-text]")).toBeNull();
});

test("the words a turn streams between steps stand above its working line, one passage at a time", async () => {
  wire(transcript({ messages: [{ role: "user", text: "Review PR 1268." }], turn: TURN_ID }));
  open();

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("activity", { text: "Reviewing the pull request." });
  StreamFake.last().emit("message", { text: "Let me read " });
  StreamFake.last().emit("message", { text: "the diff." });
  StreamFake.last().emit("activity", { text: "Loading coding guidance." });

  const said = await screen.findByText("Let me read the diff.");
  expect(said.closest("[data-slot=interstitial]")).toBeTruthy();
  expect(order("Let me read the diff.", "Loading coding guidance.")).toBe(true);

  StreamFake.last().emit("message", { text: "Now the tests." });
  StreamFake.last().emit("activity", { text: "Running the focused tests." });

  expect(await screen.findByText("Now the tests.")).toBeTruthy();
  expect(screen.queryByText("Let me read the diff.")).toBeNull();
  expect(screen.queryByText(/Let me read the diff.Now the tests./)).toBeNull();
});

test("the words above the working line go as soon as the turn speaks again, and the answer stands alone", async () => {
  wire(transcript({ messages: [{ role: "user", text: "Review PR 1268." }], turn: TURN_ID }));
  open();

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("activity", { text: "Reviewing the pull request." });
  StreamFake.last().emit("message", { text: "Let me read the diff." });
  StreamFake.last().emit("activity", { text: "Loading coding guidance." });
  expect(await screen.findByText("Let me read the diff.")).toBeTruthy();

  StreamFake.last().emit("message", { text: "The change is safe." });

  await waitFor(() => expect(screen.queryByText("Let me read the diff.")).toBeNull());
  expect(screen.getByText(saying("The change is safe."))).toBeTruthy();

  StreamFake.last().emit("terminal", {
    status: "done",
    text: "The change is safe.",
    model: "opus",
    tokens: 9,
    cost_micro_usd: 1_000_000,
  });

  expect(await screen.findByText(saying("The change is safe."))).toBeTruthy();
  expect(document.querySelector("[data-slot=interstitial]")).toBeNull();
  expect(document.querySelector("[data-slot=marker] [data-slot=decode-text]")).toBeNull();
});

test("a stopped turn records the words it streamed before the step it was on", async () => {
  wire(transcript({ messages: [{ role: "user", text: "Review PR 1268." }], turn: TURN_ID }));
  open();

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("message", { text: "Let me read the diff." });
  StreamFake.last().emit("activity", { text: "Loading coding guidance." });
  expect(await screen.findByText("Let me read the diff.")).toBeTruthy();

  StreamFake.last().emit("terminal", { status: "cancelled" });

  expect(await screen.findByText(saying(/Let me read the diff\.\s+Stopped\./))).toBeTruthy();
  expect(document.querySelector("[data-slot=interstitial]")).toBeNull();
});

test("a failed turn keeps the words it streamed beside the failure", async () => {
  wire(transcript({ messages: [{ role: "user", text: "Review PR 1268." }], turn: TURN_ID }));
  open();

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("message", { text: "Let me read the diff." });
  StreamFake.last().emit("activity", { text: "Loading coding guidance." });
  expect(await screen.findByText("Let me read the diff.")).toBeTruthy();

  StreamFake.last().emit("terminal", { status: "failed", error_class: "ProviderTimeout" });

  expect(
    await screen.findByText(saying(/Let me read the diff\.\s+\(failed: ProviderTimeout\)/)),
  ).toBeTruthy();
});

test("a late activity label leaves the whole answer in the reply the turn records", async () => {
  wire(transcript({ messages: [{ role: "user", text: "Review PR 1268." }], turn: TURN_ID }));
  open();

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("message", { text: "The change is safe." });
  StreamFake.last().emit("activity", { text: "Loading coding guidance." });
  StreamFake.last().emit("message", { text: "Ship it." });

  const tail = await screen.findByText(saying("Ship it."));
  expect(tail.closest("[data-slot=interstitial]")).toBeNull();
  expect(document.querySelector("[data-slot=interstitial]")).toBeNull();

  StreamFake.last().emit("terminal", { status: "cancelled" });

  expect(await screen.findByText(saying(/The change is safe\.\s+Ship it\.\s+Stopped\./))).toBeTruthy();
});

test("a settled reply draws no row for the run that fed it", async () => {
  const conversationId = "66666666-6666-4666-8666-666666666666";
  wire(transcript({ messages: [{ role: "user", text: "Research it." }], turn: TURN_ID }));
  open();

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("subagent", {
    profile: "general_purpose",
    conversation_id: conversationId,
    events: [{ kind: "activity", text: "Fetching the page." }],
    output: "The release shipped on Tuesday.",
    subagents: [],
  });
  StreamFake.last().emit("terminal", {
    status: "done",
    text: "It shipped Tuesday.",
    model: "opus",
    tokens: 9,
    cost_micro_usd: 1_000_000,
  });

  expect(await screen.findByText(saying("It shipped Tuesday."))).toBeTruthy();
  expect(screen.queryByText("Subagent · general_purpose")).toBeNull();
  expect(screen.queryByText("Fetching the page.")).toBeNull();
  expect(screen.queryByText("The release shipped on Tuesday.")).toBeNull();
});

test("a live run puts the wait on the working line, and gives the line back when it ends", async () => {
  const conversationId = "66666666-6666-4666-8666-666666666666";
  const childTurn = "88888888-8888-4888-8888-888888888888";
  const frame = {
    turn_id: childTurn,
    parent_turn_id: TURN_ID,
    conversation_id: conversationId,
    profile: "general_purpose",
    name: "UK sports news",
    activity: "",
    status: "",
  };
  wire(transcript({ messages: [{ role: "user", text: "Research it." }], turn: TURN_ID }));
  open();

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("activity", { text: "Handing the research off." });
  StreamFake.last().emit("subagent_activity", frame);
  expect(await screen.findByText("Awaiting 1 subagent")).toBeTruthy();
  expect(screen.queryByText("Handing the research off.")).toBeNull();
  expect(screen.getByText("UK sports news")).toBeTruthy();

  StreamFake.last().emit("subagent_activity", {
    ...frame,
    activity: "Searching for latest MLS news.",
  });
  expect(await screen.findByText("Searching for latest MLS news.")).toBeTruthy();
  expect(screen.getByText("Awaiting 1 subagent")).toBeTruthy();
  expect(screen.queryByText("UK sports news")).toBeNull();

  StreamFake.last().emit("subagent_activity", { ...frame, status: "done" });
  await waitFor(() => expect(screen.queryByText("Awaiting 1 subagent")).toBeNull());
  expect(screen.queryByText("Searching for latest MLS news.")).toBeNull();
  expect(screen.getByText("Handing the research off.")).toBeTruthy();
});

const FIRST_RUN = {
  turn_id: "88888888-8888-4888-8888-888888888888",
  parent_turn_id: TURN_ID,
  conversation_id: "66666666-6666-4666-8666-666666666666",
  profile: "general_purpose",
  name: "Lookup",
  activity: "",
  status: "",
};

const SECOND_RUN = {
  ...FIRST_RUN,
  turn_id: "99999999-9999-4999-8999-999999999999",
  conversation_id: "77777777-7777-4777-8777-777777777777",
  name: "Issues",
};

test("each subagent the turn waits on states what it is doing, under the count", async () => {
  wire(transcript({ messages: [{ role: "user", text: "File it." }], turn: TURN_ID }));
  open();

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("subagent_activity", { ...FIRST_RUN, activity: "Looking up info" });
  StreamFake.last().emit("subagent_activity", { ...SECOND_RUN, activity: "Naming the new issue" });

  expect(await screen.findByText("Awaiting 2 subagents")).toBeTruthy();
  expect(screen.getByText("Looking up info")).toBeTruthy();
  expect(screen.getByText("Naming the new issue")).toBeTruthy();

  StreamFake.last().emit("subagent_activity", { ...FIRST_RUN, status: "done" });

  expect(await screen.findByText("Awaiting 1 subagent")).toBeTruthy();
  expect(screen.queryByText("Looking up info")).toBeNull();
  expect(screen.getByText("Naming the new issue")).toBeTruthy();
});

test("a conversation reloaded mid-run draws the waiting runs the replayed frames rebuild", async () => {
  wire(transcript({ messages: [{ role: "user", text: "File it." }], turn: TURN_ID }));
  open();

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("subagent_activity", { ...FIRST_RUN, activity: "Looking up info" });
  expect(await screen.findByText("Awaiting 1 subagent")).toBeTruthy();

  cleanup();
  resetStreams();
  resetChatStore();
  open();

  await waitFor(() => expect(StreamFake.opened.length).toBe(2));
  StreamFake.last().emit("subagent_activity", { ...FIRST_RUN, activity: "Looking up info" });

  expect(await screen.findByText("Awaiting 1 subagent")).toBeTruthy();
  expect(screen.getAllByText("Looking up info")).toHaveLength(1);
});

const LOOKUP_NODE = {
  profile: "general_purpose",
  name: "Lookup",
  conversation_id: FIRST_RUN.conversation_id,
  events: [],
  output: "",
  subagents: [],
};

test("the wait stands through a message sent mid-run and the answer that message gets", async () => {
  wire({
    ...transcript({ messages: [{ role: "user", text: "Research it." }], turn: TURN_ID }),
    "/chat": () => json({ ...FOLDED, arrival_id: ARRIVAL_ID }),
  });
  open();

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("subagent_activity", FIRST_RUN);
  expect(await screen.findByText("Awaiting 1 subagent")).toBeTruthy();

  await userEvent.type(screen.getByLabelText("Ask UFO"), "and the tags?");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  expect(await screen.findByText("and the tags?")).toBeTruthy();
  StreamFake.last().emit("absorbed", { arrivals: [ARRIVAL_ID] });
  StreamFake.last().emit("subagent", { ...LOOKUP_NODE, running: true });
  StreamFake.last().emit("terminal", {
    status: "done",
    text: "Tagged v2; the lookup runs on.",
    model: "opus",
    tokens: 9,
    cost_micro_usd: 1_000_000,
  });

  expect(await screen.findByText("Tagged v2; the lookup runs on.")).toBeTruthy();
  expect(screen.getByText("Awaiting 1 subagent")).toBeTruthy();
  expect(screen.getByText("Lookup")).toBeTruthy();
});

test("the wait goes when the read says the run the turn left going has ended", async () => {
  let serves = 0;
  const answer = "Tagged v2; the lookup runs on.";
  wire({
    ...transcript(),
    "/transcript": () => {
      serves += 1;
      return json(
        serves === 1
          ? { messages: [{ role: "user", text: "Research it." }], turn: TURN_ID }
          : {
              messages: [
                {
                  role: "assistant",
                  text: answer,
                  subagents: [{ ...LOOKUP_NODE, running: false }],
                },
              ],
            },
      );
    },
  });
  open();

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("subagent_activity", { ...FIRST_RUN, activity: "Looking up info" });
  StreamFake.last().emit("subagent", { ...LOOKUP_NODE, running: true });
  StreamFake.last().emit("terminal", {
    status: "done",
    text: answer,
    model: "opus",
    tokens: 9,
    cost_micro_usd: 1_000_000,
  });
  expect(await screen.findByText("Awaiting 1 subagent")).toBeTruthy();

  window.dispatchEvent(new Event("focus"));

  await waitFor(() => expect(screen.queryByText("Awaiting 1 subagent")).toBeNull());
  expect(screen.getByText(answer)).toBeTruthy();
});

test("the wait states itself on the turn whose run is going, not on the newest one", () => {
  render(
    <TranscriptScroll>
      <MessageLog
        messages={[
          { role: "user", text: "Research it." },
          {
            role: "assistant",
            text: "Handed it off.",
            subagents: [{ ...LOOKUP_NODE, running: true }],
          },
          { role: "user", text: "And the tags?" },
          { role: "assistant", text: "Tagged v2." },
        ]}
        live={liveTurn()}
      />
    </TranscriptScroll>,
  );

  expect(screen.getAllByText("Awaiting 1 subagent")).toHaveLength(1);
  expect(order("Awaiting 1 subagent", "And the tags?")).toBe(true);
  expect(order("Tagged v2.", "Researching…")).toBe(true);
});

function order(first: string, second: string): boolean {
  const log = document.body.textContent ?? "";
  return log.indexOf(first) < log.indexOf(second);
}

test("a message that opens a turn stands above the reply it is waiting for", async () => {
  let land: (payload: unknown) => void = () => {};
  const admitted = new Promise<Response>((resolve) => {
    land = (payload) => resolve(json(payload));
  });
  wire({ ...transcript(), "/chat": () => admitted });
  open();

  await screen.findByText("No messages in this conversation yet.");
  await userEvent.type(screen.getByLabelText("Ask UFO"), "write a poem");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  expect(await screen.findByText("write a poem")).toBeTruthy();
  expect(order("write a poem", "Researching…")).toBe(true);

  land({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "poem" });
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("message", { text: "Ducks glide at dusk." });

  expect(await screen.findByText(saying("Ducks glide at dusk."))).toBeTruthy();
  expect(order("write a poem", "Ducks glide at dusk.")).toBe(true);
});

test("a queued message stays under the answer streaming above it, and never moves", async () => {
  wire({
    ...transcript({ messages: [{ role: "user", text: "write another poem" }], turn: TURN_ID }),
    "/chat": () => json({ ...FOLDED, arrival_id: ARRIVAL_ID }),
  });
  open();

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("reply", { id: REPLY_ID, text: "Ducks glide at dusk." });
  expect(await screen.findByText(saying("Ducks glide at dusk."))).toBeTruthy();

  await userEvent.type(screen.getByLabelText("Ask UFO"), "4+4=");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  expect(await screen.findByText("4+4=")).toBeTruthy();
  await waitFor(() => expect(order("Ducks glide at dusk.", "4+4=")).toBe(true));

  StreamFake.last().emit("absorbed", { arrivals: [ARRIVAL_ID] });
  StreamFake.last().emit("message", { text: "8" });
  StreamFake.last().emit("terminal", {
    status: "done",
    text: "8",
    model: "opus",
    tokens: 3,
    cost_micro_usd: 1_000,
  });

  await screen.findByText("8");
  expect(order("write another poem", "Ducks glide at dusk.")).toBe(true);
  expect(order("Ducks glide at dusk.", "4+4=")).toBe(true);
  expect(order("4+4=", "8")).toBe(true);
});

test("a message sent mid-turn joins the running turn, waits to be taken up, and is tailed once", async () => {
  const { calls } = wire({
    ...transcript({ messages: [{ role: "user", text: "Review PR 1268." }], turn: TURN_ID }),
    "/chat": () => json({ ...FOLDED, arrival_id: ARRIVAL_ID }),
  });
  open();

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("message", { text: "reading it" });
  expect(await screen.findByText(saying("reading it"))).toBeTruthy();

  await userEvent.type(screen.getByLabelText("Ask UFO"), "and again");
  const send = screen.getByRole("button", { name: "Send" });
  expect((send as HTMLButtonElement).disabled).toBe(false);
  await userEvent.click(send);
  await waitFor(() =>
    expect(calls.filter((url) => url.includes("/chat?conversation=")).length).toBe(1),
  );
  expect(StreamFake.opened.length).toBe(1);
  await waitFor(() => expect(waiting("and again")).toBe(true));
  expect(screen.getByText(saying("reading it"))).toBeTruthy();

  StreamFake.last().emit("absorbed", { arrivals: [ARRIVAL_ID] });
  await waitFor(() => expect(waiting("and again")).toBe(false));

  StreamFake.last().emit("terminal", {
    status: "done",
    text: "Reviewed it.",
    model: "opus",
    tokens: 9,
    cost_micro_usd: 1_000_000,
  });

  await screen.findByText("Reviewed it.");
  expect(StreamFake.opened.length).toBe(1);
});

test("a reply the turn delivered before the fold stands ahead of the message it folded", async () => {
  wire({
    ...transcript({ messages: [{ role: "user", text: "write a poem" }], turn: TURN_ID }),
    "/chat": () => json({ ...FOLDED, arrival_id: ARRIVAL_ID }),
  });
  open();

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("reply", { id: REPLY_ID, text: "Ducks glide at dusk." });
  expect(await screen.findByText(saying("Ducks glide at dusk."))).toBeTruthy();

  await userEvent.type(screen.getByLabelText("Ask UFO"), "1+1=");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(waiting("1+1=")).toBe(true));

  StreamFake.last().emit("absorbed", { arrivals: [ARRIVAL_ID] });
  StreamFake.last().emit("message", { text: "2" });
  StreamFake.last().emit("terminal", {
    status: "done",
    text: "2",
    model: "opus",
    tokens: 3,
    cost_micro_usd: 1_000,
  });

  await screen.findByText("2");
  expect(screen.getByText(saying("Ducks glide at dusk."))).toBeTruthy();
  const log = document.body.textContent ?? "";
  expect(log.indexOf("Ducks glide at dusk.")).toBeGreaterThan(log.indexOf("write a poem"));
  expect(log.indexOf("1+1=")).toBeGreaterThan(log.indexOf("Ducks glide at dusk."));
});

test("working narration is not a reply: a drain clears it rather than settling it", async () => {
  wire({
    ...transcript({ messages: [{ role: "user", text: "write a poem" }], turn: TURN_ID }),
    "/chat": () => json({ ...FOLDED, arrival_id: ARRIVAL_ID }),
  });
  open();

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("message", { text: "Thinking about ducks." });
  expect(await screen.findByText(saying("Thinking about ducks."))).toBeTruthy();

  StreamFake.last().emit("absorbed", { arrivals: [ARRIVAL_ID] });
  StreamFake.last().emit("message", { text: "2" });
  StreamFake.last().emit("terminal", {
    status: "done",
    text: "2",
    model: "opus",
    tokens: 3,
    cost_micro_usd: 1_000,
  });

  await screen.findByText("2");
  expect(screen.queryByText(saying("Thinking about ducks."))).toBeNull();
});

test("a drain that beats the response leaves the reply above the message", async () => {
  let land: (payload: unknown) => void = () => {};
  const admitted = new Promise<Response>((resolve) => {
    land = (payload) => resolve(json(payload));
  });
  wire({
    ...transcript({ messages: [{ role: "user", text: "write a poem" }], turn: TURN_ID }),
    "/chat": () => admitted,
  });
  open();

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("reply", { id: REPLY_ID, text: "Ducks glide at dusk." });
  expect(await screen.findByText(saying("Ducks glide at dusk."))).toBeTruthy();

  await userEvent.type(screen.getByLabelText("Ask UFO"), "1+1=");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  expect(await screen.findByText("1+1=")).toBeTruthy();

  StreamFake.last().emit("absorbed", { arrivals: [ARRIVAL_ID] });
  land({ ...FOLDED, arrival_id: ARRIVAL_ID });
  StreamFake.last().emit("message", { text: "2" });
  StreamFake.last().emit("terminal", {
    status: "done",
    text: "2",
    model: "opus",
    tokens: 3,
    cost_micro_usd: 1_000,
  });

  await screen.findByText("2");
  const log = document.body.textContent ?? "";
  expect(log.indexOf("1+1=")).toBeGreaterThan(log.indexOf("Ducks glide at dusk."));
  await waitFor(() => expect(waiting("1+1=")).toBe(false));
});

test("each fold clears its own message, and the replies between them keep their order", async () => {
  const SECOND_ARRIVAL = "99999999-9999-4999-8999-999999999999";
  const SECOND_REPLY = "77777777-7777-4777-8777-777777777777";
  const responses: ((payload: unknown) => void)[] = [];
  wire({
    ...transcript({ messages: [{ role: "user", text: "write a poem" }], turn: TURN_ID }),
    "/chat": () => new Promise<Response>((resolve) => responses.push((p) => resolve(json(p)))),
  });
  open();

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));

  await userEvent.type(screen.getByLabelText("Ask UFO"), "1+1=");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  expect(await screen.findByText("1+1=")).toBeTruthy();
  StreamFake.last().emit("absorbed", { arrivals: [ARRIVAL_ID] });
  StreamFake.last().emit("reply", { id: REPLY_ID, text: "The answer is 2." });
  expect(await screen.findByText(saying("The answer is 2."))).toBeTruthy();
  responses[0]({ ...FOLDED, arrival_id: ARRIVAL_ID });
  await waitFor(() => expect(waiting("1+1=")).toBe(false));

  await userEvent.type(screen.getByLabelText("Ask UFO"), "2+2=");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  expect(await screen.findByText("2+2=")).toBeTruthy();
  StreamFake.last().emit("absorbed", { arrivals: [SECOND_ARRIVAL] });
  responses[1]({ ...FOLDED, arrival_id: SECOND_ARRIVAL });
  StreamFake.last().emit("reply", { id: SECOND_REPLY, text: "And that is 4." });

  expect(await screen.findByText(saying("And that is 4."))).toBeTruthy();
  await waitFor(() => expect(waiting("2+2=")).toBe(false));
  const log = document.body.textContent ?? "";
  expect(log.indexOf("The answer is 2.")).toBeGreaterThan(log.indexOf("1+1="));
  expect(log.indexOf("2+2=")).toBeGreaterThan(log.indexOf("The answer is 2."));
  expect(log.indexOf("And that is 4.")).toBeGreaterThan(log.indexOf("2+2="));
});

test("a reply frame replayed after a reconnect states its reply once", async () => {
  wire({
    ...transcript({ messages: [{ role: "user", text: "write a poem" }], turn: TURN_ID }),
    "/chat": () => json(FOLDED),
  });
  open();

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("reply", { id: REPLY_ID, text: "Ducks glide at dusk." });
  expect(await screen.findByText(saying("Ducks glide at dusk."))).toBeTruthy();

  // The hub replays frames after a cursor, and a recovered turn republishes the spans its recorded
  // rounds produced. The span's id is durable, so the page draws it once.
  StreamFake.last().emit("reply", { id: REPLY_ID, text: "Ducks glide at dusk." });

  expect(screen.getAllByText(saying("Ducks glide at dusk.")).length).toBe(1);
});

test("a drain that beats the response leaves no wait under the message it took up", async () => {
  let land: (payload: unknown) => void = () => {};
  const admitted = new Promise<Response>((resolve) => {
    land = (payload) => resolve(json(payload));
  });
  await sendingMidTurn(() => admitted);

  StreamFake.last().emit("absorbed", { arrivals: [ARRIVAL_ID] });
  land({ ...FOLDED, arrival_id: ARRIVAL_ID });
  StreamFake.last().emit("message", { text: "reading both" });

  expect(await screen.findByText(saying("reading both"))).toBeTruthy();
  expect(waiting("and again")).toBe(false);
  expect(StreamFake.opened.length).toBe(1);
});

test("a wait states nothing once the turn it was waiting on has ended", async () => {
  await sendingMidTurn(() => json({ ...FOLDED, arrival_id: ARRIVAL_ID }));
  await waitFor(() => expect(waiting("and again")).toBe(true));

  StreamFake.last().emit("terminal", { status: "failed", error_class: "ProviderTimeout" });

  expect(await screen.findByText("(failed: ProviderTimeout)")).toBeTruthy();
  await waitFor(() => expect(waiting("and again")).toBe(false));
});

test("a failed mid-turn send states the error and leaves the reply streaming", async () => {
  let fail: () => void = () => {};
  const failed = new Promise<Response>((resolve) => {
    fail = () => resolve(new Response("nope", { status: 500 }));
  });
  await sendingMidTurn(() => failed);

  StreamFake.last().emit("message", { text: "reading it" });
  expect(await screen.findByText(saying("reading it"))).toBeTruthy();
  fail();

  expect(await screen.findByText("Error 500 — try again.")).toBeTruthy();
  expect(screen.getByText(saying("reading it"))).toBeTruthy();
  expect(StreamFake.opened.length).toBe(1);

  StreamFake.last().emit("message", { text: ", and reviewed" });
  expect(await screen.findByText(saying("reading it, and reviewed"))).toBeTruthy();
});

test("a refused mid-turn message is tailed on the turn it founded, behind no live source", async () => {
  await sendingMidTurn(() =>
    json({ turn_id: REFUSED_TURN, conversation_id: CONVO_ID, title: "go", opened_run: false }),
  );

  await waitFor(() => expect(StreamFake.opened.length).toBe(2));
  expect(StreamFake.last().url).toBe("/surface/web/turns/" + REFUSED_TURN + "/stream");
  expect(StreamFake.opened[0].closed).toBe(true);

  StreamFake.last().emit("parked", { message: "Over the spend cap — raise it to carry on." });

  expect(await screen.findByText("Over the spend cap — raise it to carry on.")).toBeTruthy();
  expect(waiting("and again")).toBe(false);
});

test("a refusal states itself alone, never glued to the reply the page stopped tailing", async () => {
  let serves = 0;
  wire({
    ...transcript(),
    "/transcript": () => {
      serves += 1;
      return json(
        serves === 1
          ? { messages: [{ role: "user", text: "Review PR 1268." }], turn: TURN_ID }
          : {
              messages: [
                { role: "user", text: "Review PR 1268." },
                { role: "assistant", text: "reading it, and reviewed." },
              ],
            },
      );
    },
    "/chat": () =>
      json({ turn_id: REFUSED_TURN, conversation_id: CONVO_ID, title: "go", opened_run: false }),
  });
  open();

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("message", { text: "reading it" });
  expect(await screen.findByText(saying("reading it"))).toBeTruthy();

  await userEvent.type(screen.getByLabelText("Ask UFO"), "and again");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  await waitFor(() => expect(StreamFake.opened.length).toBe(2));
  StreamFake.last().emit("parked", { message: "Over the spend cap — raise it to carry on." });

  expect(await screen.findByText("Over the spend cap — raise it to carry on.")).toBeTruthy();
  expect(screen.queryByText(/reading it/)).toBeNull();

  window.dispatchEvent(new Event("focus"));
  expect(await screen.findByText("reading it, and reviewed.")).toBeTruthy();
});

test("a fold that resumes a parked turn is tailed again, because admission opened that run", async () => {
  await sendingMidTurn(() => json({ ...FOLDED, opened_run: true, arrival_id: ARRIVAL_ID }));

  await waitFor(() => expect(StreamFake.opened.length).toBe(2));
  expect(StreamFake.opened[0].closed).toBe(true);
  expect(StreamFake.last().url).toBe("/surface/web/turns/" + TURN_ID + "/stream");
  await waitFor(() => expect(waiting("and again")).toBe(true));

  StreamFake.last().emit("absorbed", { arrivals: [ARRIVAL_ID] });

  await waitFor(() => expect(waiting("and again")).toBe(false));
});

test("a settled conversation tails nothing", async () => {
  wire(
    transcript({
      messages: [
        { role: "user", text: "inspect it" },
        { role: "assistant", text: "The tests pass." },
      ],
    }),
  );
  open();

  expect(await screen.findByText("The tests pass.")).toBeTruthy();
  expect(StreamFake.opened.length).toBe(0);
  expect(screen.queryByText("Researching…")).toBeNull();
});

test("a conversation opens its file changes and returns to chat", async () => {
  wire({
    ["/conversations/" + CONVO_ID + "/slots/changes"]: () =>
      json({
        type: "changes",
        changes: [
          {
            path: "/workspace/demo.py",
            patch: "--- before\n+++ after\n@@ -1 +1 @@\n-old demo\n+new demo",
            truncated: false,
          },
          {
            path: "/workspace/ufo/src/answer.ts",
            patch: "--- before\n+++ after\n@@ -1 +1 @@\n-old\n+new",
            truncated: true,
          },
        ],
        truncated: true,
      }),
    ...transcript(),
  });
  open();

  await userEvent.click(await screen.findByRole("button", { name: "Changes" }));
  expect(location.hash).toBe("#/c/" + CONVO_ID + "?slot=changes");
  const sheet = await screen.findByRole("dialog", { name: "Changes" });
  expect(await screen.findByText("/workspace/ufo/src/answer.ts")).toBeTruthy();
  const column = screen.getByTestId("log").closest("[data-slot=message-scroller]")!.parentElement!;
  const beside = sheet.closest("[data-slot=resizable-panel]")!;
  expect(screen.getByRole("main").contains(beside)).toBe(true);
  expect(beside.contains(column)).toBe(false);
  expect(column.closest("[data-slot=slot-track]")?.className).toContain("contents");
  expect(document.querySelector('[data-slot-icon="diff"]')).toBeTruthy();
  expect(screen.getByText("-old").className).toContain("bg-attention");
  expect(screen.getByText("+new").className).toContain("bg-affirm");
  expect(
    screen.getAllByText("--- before").every((line) => !line.className.includes("bg-attention")),
  ).toBe(true);
  expect(screen.getByText("This diff is truncated.")).toBeTruthy();
  expect(screen.getByText("Some changes may not be shown.")).toBeTruthy();

  await userEvent.click(within(sheet).getByRole("button", { name: "Close" }));
  expect(location.hash).toBe("#/c/" + CONVO_ID);
});

test("multiple slot types coexist and sources render as external links", async () => {
  wire({
    ["/conversations/" + CONVO_ID + "/slots/sources"]: () =>
      json({
        type: "sources",
        sources: [
          {
            url: "https://example.com/report",
            title: "Quarterly report",
            snippet: "**The retrieved result.**",
            published_date: "2026-08-01",
          },
        ],
        truncated: false,
      }),
    ...transcript(),
    "/slots": () =>
      json({
        slots: [
          { id: "changes", label: "Changes", icon: "diff", kind: "changes", count: 2 },
          { id: "sources", label: "Sources", icon: "link", kind: "sources", count: 1 },
        ],
      }),
  });
  open();

  const changes = await screen.findByRole("button", { name: "Changes 2" });
  const sources = screen.getByRole("button", { name: "Sources 1" });
  const band = screen.getByRole("main").querySelector("[data-slot=header]") as HTMLElement;
  const row = band.firstElementChild as HTMLElement;
  expect(band.children).toHaveLength(1);
  expect(row.children).toHaveLength(2);
  expect(row.children[1].contains(changes)).toBe(true);
  expect(row.children[1].contains(sources)).toBe(true);
  expect(changes.textContent).toBe("");
  expect(changes.querySelector('[data-slot-icon="diff"]')).toBeTruthy();
  expect(sources.textContent).toBe("");
  expect(sources.querySelector('[data-slot-icon="link"]')).toBeTruthy();
  expect(sources.getAttribute("aria-pressed")).toBe("false");

  await userEvent.click(sources);
  expect(sources.getAttribute("aria-pressed")).toBe("true");

  expect(location.hash).toBe("#/c/" + CONVO_ID + "?slot=sources");
  const source = await screen.findByRole("link", { name: "Quarterly report" });
  expect(source.getAttribute("href")).toBe("https://example.com/report");
  expect(source.getAttribute("target")).toBe("_blank");
  const summary = screen.getByText("Summary");
  const disclosure = summary.closest("details") as HTMLDetailsElement;
  expect(disclosure.open).toBe(false);
  await userEvent.click(summary);
  expect(disclosure.open).toBe(true);
  expect(screen.getByText("The retrieved result.").closest("strong")).toBeTruthy();
});

test("an artifact slot file opens the shared file sheet", async () => {
  const url = "https://ufo.example/artifacts/chart.png?token=signed";
  wire({
    ["/conversations/" + CONVO_ID + "/slots/artifacts"]: () =>
      json({
        type: "artifacts",
        artifacts: [
          {
            filename: "chart.png",
            subject: "The final chart",
            media_type: "image/png",
            size_bytes: 2048,
            created_at: "2026-08-06T12:00:00Z",
            url,
            preview: {
              type: "image",
              media_type: "image/png",
              url: "/artifacts/preview/chart.png?token=signed",
            },
          },
        ],
        truncated: false,
      }),
    ...transcript(),
    "/slots": () =>
      json({
        slots: [
          {
            id: "artifacts",
            label: "Artifacts",
            icon: "artifact",
            kind: "artifacts",
            count: 1,
          },
        ],
      }),
  });
  open();

  await userEvent.click(await screen.findByRole("button", { name: "Artifacts 1" }));
  const sheet = await screen.findByRole("dialog", { name: "Artifacts" });
  const artifact = await within(sheet).findByRole("button", { name: "chart.png" });
  expect(within(sheet).queryByRole("link", { name: "chart.png" })).toBeNull();
  expect(screen.getByText("The final chart")).toBeTruthy();
  expect(sheet.querySelector('img[src="/artifacts/preview/chart.png?token=signed"]')).toBeTruthy();

  await userEvent.click(artifact);

  const file = await screen.findByRole("dialog", { name: "chart.png" });
  expect(within(file).getByText("The final chart · image/png · 2 kB")).toBeTruthy();
  expect(within(file).getByRole("link", { name: "Download" }).getAttribute("href")).toBe(url);
  expect(file.querySelector('img[src="/artifacts/preview/chart.png?token=signed"]')).toBeTruthy();
});

test("a markdown artifact opens as the full document in the shared file sheet", async () => {
  const notes = "https://ufo.example/artifacts/notes.md?token=signed";
  const preview = "https://ufo.example/artifacts/preview/notes.png?token=signed";
  let bodyReads = 0;
  wire({
    ["/conversations/" + CONVO_ID + "/slots/artifacts"]: () =>
      json({
        type: "artifacts",
        artifacts: [
          {
            filename: "notes.md",
            subject: "The written summary",
            media_type: "text/markdown",
            size_bytes: 24,
            created_at: "2026-08-06T12:00:00Z",
            url: notes,
            preview: {
              filename: "notes.png",
              media_type: "image/png",
              url: preview,
            },
          },
          {
            filename: "deck.pdf",
            subject: null,
            media_type: "application/pdf",
            size_bytes: 4096,
            created_at: "2026-08-06T11:00:00Z",
            url: "https://ufo.example/artifacts/deck.pdf?token=signed",
            preview: null,
          },
        ],
        truncated: false,
      }),
    [notes]: () => {
      bodyReads += 1;
      return new Response("# Findings\n\nThe number moved.");
    },
    ...transcript(),
    "/slots": () =>
      json({
        slots: [
          { id: "artifacts", label: "Artifacts", icon: "artifact", kind: "artifacts", count: 2 },
        ],
      }),
  });
  open();

  await userEvent.click(await screen.findByRole("button", { name: "Artifacts 2" }));
  expect(await screen.findByText("The written summary")).toBeTruthy();
  expect(bodyReads).toBe(0);

  await userEvent.click(screen.getByRole("button", { name: "notes.md" }));

  const heading = await screen.findByRole("heading", { name: "Findings" });
  expect(await screen.findByText(/The number moved\./)).toBeTruthy();
  expect(bodyReads).toBe(1);
  expect(heading.tagName).toBe("H1");
  expect(screen.queryByText(/# Findings/)).toBeNull();
  const document = heading.closest("[data-artifact-document]");
  expect(document?.className).not.toContain("max-h-24");
  expect(screen.queryByRole("img", { name: "The written summary" })).toBeNull();
  expect(document?.querySelector('img[src="' + preview + '"]')).toBeNull();
  expect(screen.getByRole("link", { name: "Download" }).getAttribute("href")).toBe(notes);
});

test("a plain text artifact keeps its characters instead of being read as markdown", async () => {
  const notes = "https://ufo.example/artifacts/notes.txt?token=signed";
  wire({
    ["/conversations/" + CONVO_ID + "/slots/artifacts"]: () =>
      json({
        type: "artifacts",
        artifacts: [
          {
            filename: "notes.txt",
            subject: null,
            media_type: "text/plain",
            size_bytes: 32,
            created_at: "2026-08-06T12:00:00Z",
            url: notes,
            preview: null,
          },
        ],
        truncated: false,
      }),
    [notes]: () => new Response("# Findings\n\n* not a list item"),
    ...transcript(),
    "/slots": () =>
      json({
        slots: [
          { id: "artifacts", label: "Artifacts", icon: "artifact", kind: "artifacts", count: 1 },
        ],
      }),
  });
  open();

  await userEvent.click(await screen.findByRole("button", { name: "Artifacts 1" }));
  await userEvent.click(await screen.findByRole("button", { name: "notes.txt" }));
  const preformatted = await screen.findByText(/# Findings/);
  expect(preformatted.tagName).toBe("PRE");
  expect(preformatted.textContent).toContain("* not a list item");
  expect(document.querySelector("aside h1")).toBeNull();
  expect(document.querySelector("aside li")).toBeNull();
});

test("tasks slot renders durable checklist progress and task status", async () => {
  wire({
    ["/conversations/" + CONVO_ID + "/slots/tasks"]: () =>
      json({
        type: "tasks",
        title: "Ship slots",
        tasks: [
          { description: "Define the payload", status: "completed" },
          { description: "Render the board", status: "in_progress" },
          { description: "Verify the flow", status: "pending" },
        ],
        total_count: 3,
        completed_count: 1,
        truncated: false,
      }),
    ...transcript(),
    "/slots": () =>
      json({
        slots: [{ id: "tasks", label: "Tasks", icon: "task", kind: "tasks", count: 3 }],
      }),
  });
  open();

  await userEvent.click(await screen.findByRole("button", { name: "Tasks 3" }));
  expect(await screen.findByRole("heading", { name: "Ship slots" })).toBeTruthy();
  expect(screen.getByText("1 of 3 completed.")).toBeTruthy();
  expect(screen.getByText("Define the payload")).toBeTruthy();
  expect(screen.getByText("Completed")).toBeTruthy();
  expect(screen.getByText("In progress")).toBeTruthy();
  expect(screen.getByText("Pending")).toBeTruthy();
});

test("tasks slot preserves an empty truncated board's context", async () => {
  wire({
    ["/conversations/" + CONVO_ID + "/slots/tasks"]: () =>
      json({
        type: "tasks",
        title: "Bounded board",
        tasks: [],
        total_count: 0,
        completed_count: 0,
        truncated: true,
      }),
    ...transcript(),
    "/slots": () =>
      json({
        slots: [{ id: "tasks", label: "Tasks", icon: "task", kind: "tasks", count: 0 }],
      }),
  });
  open();

  await waitFor(() =>
    expect(document.querySelector("[data-slot=header]")?.textContent).toBeTruthy(),
  );
  const band = document.querySelector<HTMLElement>("[data-slot=header]")!;
  await userEvent.click(await within(band).findByRole("button", { name: "Tasks" }));
  expect(await screen.findByRole("heading", { name: "Bounded board" })).toBeTruthy();
  expect(screen.getByText("0 of 0 completed.")).toBeTruthy();
  expect(await screen.findByText("No tasks.")).toBeTruthy();
  expect(screen.getByText("Some tasks may not be shown.")).toBeTruthy();
});

test("sites slot renders hosted links with when they were made and last changed", async () => {
  const url = "https://ufo.example/sites/signed";
  wire({
    ["/conversations/" + CONVO_ID + "/slots/sites"]: () =>
      json({
        type: "sites",
        sites: [
          {
            name: "team-dashboard",
            url,
            visibility: "workspace",
            created_at: "2026-08-01T12:00:00Z",
            updated_at: "2026-08-06T12:00:00Z",
          },
        ],
        truncated: false,
      }),
    ...transcript(),
    "/slots": () =>
      json({
        slots: [{ id: "sites", label: "Sites", icon: "link", kind: "sites", count: 1 }],
      }),
  });
  open();

  await userEvent.click(await screen.findByRole("button", { name: "Sites 1" }));
  const sheet = await screen.findByRole("dialog", { name: "Sites" });
  expect(await within(sheet).findByText("team-dashboard")).toBeTruthy();
  expect(within(sheet).getByText(/^Created/)).toBeTruthy();
  expect(within(sheet).queryByText(/workspace/)).toBeNull();
  const link = screen.getByRole("link", { name: "Open site" });
  expect(link.getAttribute("href")).toBe(url);
  expect(link.getAttribute("target")).toBe("_blank");
});

test("automations slot renders cadence, state, and visible description", async () => {
  wire({
    ["/conversations/" + CONVO_ID + "/slots/automations"]: () =>
      json({
        type: "automations",
        automations: [
          {
            name: "daily-brief",
            description: "Send the morning brief.",
            schedule: "0 9 * * *",
            paused: false,
            next_run_at: "2026-08-08T09:00:00Z",
            last_run_at: "2026-08-07T09:00:00Z",
            latest_status: "done",
            latest_response: "Brief delivered.",
            created_at: "2026-08-01T12:00:00Z",
            updated_at: "2026-08-06T12:00:00Z",
          },
        ],
        truncated: false,
      }),
    ...transcript(),
    "/slots": () =>
      json({
        slots: [
          {
            id: "automations",
            label: "Automations",
            icon: "calendar",
            kind: "automations",
            count: 1,
          },
        ],
      }),
  });
  open();

  await userEvent.click(await screen.findByRole("button", { name: "Automations 1" }));
  expect(await screen.findByText("daily-brief")).toBeTruthy();
  expect(screen.getByText("Send the morning brief.")).toBeTruthy();
  expect(screen.getByText("Running")).toBeTruthy();
  expect(screen.getByText("Brief delivered.")).toBeTruthy();
  const lastRun = document.querySelector('time[datetime="2026-08-07T09:00:00Z"]')!.parentElement!;
  expect(lastRun.textContent).toMatch(/^Last .+ · done$/);
  expect(screen.getByText(/0 9 \* \* \* · Next/)).toBeTruthy();
  expect(document.querySelector('[data-slot-icon="calendar"]')).toBeTruthy();
});

test.each([
  [200, { type: "changes", changes: [], truncated: false }, "No changes."],
  [200, { type: "changes", changes: [], truncated: true }, "Some changes may not be shown."],
  [404, null, "This conversation is not shared with you."],
])("changes renders status %s", async (status, payload, message) => {
  location.hash = conversationSlotHash(AGENT.id, CONVO_ID, "changes");
  wire({
    ["/conversations/" + CONVO_ID + "/slots/changes"]: () =>
      payload === null ? new Response("no", { status }) : json(payload),
    ...transcript(),
  });
  open();

  expect(await screen.findByText(message)).toBeTruthy();
  expect(screen.getByText("Changes")).toBeTruthy();
});

test("changes poll after another file result lands", async () => {
  location.hash = conversationSlotHash(AGENT.id, CONVO_ID, "changes");
  let loads = 0;
  wire({
    ["/conversations/" + CONVO_ID + "/slots/changes"]: () => {
      loads += 1;
      return json({
        type: "changes",
        changes:
          loads === 1
            ? []
            : [{ path: "src/late.ts", patch: "+export const ready = true;", truncated: false }],
        truncated: false,
      });
    },
    ...transcript(),
  });
  vi.useFakeTimers();
  try {
    open();
    expect(await vi.waitFor(() => screen.getByText("No changes."))).toBeTruthy();
    await vi.advanceTimersByTimeAsync(30_000);
    expect(await vi.waitFor(() => screen.getByText("src/late.ts"))).toBeTruthy();
    expect(loads).toBe(2);
  } finally {
    vi.useRealTimers();
  }
});

test("slot counts poll when a turn settles", async () => {
  let slotLoads = 0;
  wire({
    ...transcript(),
    "/slots": () => {
      slotLoads += 1;
      return json({
        slots: [
          {
            id: "changes",
            label: "Changes",
            icon: "diff",
            kind: "changes",
            count: slotLoads === 1 ? 0 : 1,
          },
        ],
      });
    },
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "edit" }),
  });
  open();

  await screen.findByRole("button", { name: "Changes" });
  await userEvent.type(screen.getByLabelText("Ask UFO"), "edit it");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("terminal", {
    status: "done",
    model: "opus",
    tokens: 1,
    cost_micro_usd: 0,
  });

  expect(await screen.findByRole("button", { name: "Changes 1" })).toBeTruthy();
  expect(slotLoads).toBe(2);
});

test("a slot URL opens a conversation absent from the chat rail", async () => {
  const child = "66666666-6666-4666-8666-666666666666";
  const root = "77777777-7777-4777-8777-777777777777";
  location.hash = conversationSlotHash(AGENT.id, child, "changes", root);
  wire({
    ["/conversations/" + child + "/slots/changes?root=" + root]: () =>
      json({
        type: "changes",
        changes: [{ path: "/workspace/repo/child.py", patch: "+child\n", truncated: false }],
        truncated: false,
      }),
  });
  open();

  expect(await screen.findByText("/workspace/repo/child.py")).toBeTruthy();
});

test("Shift+Enter opens a line in the message and Enter sends the whole of it", async () => {
  const posted: string[] = [];
  wire({
    ...transcript(),
    "/chat": (_url, init) => {
      posted.push(String(init?.body));
      return json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "two lines" });
    },
  });
  open();
  await screen.findByText("No messages in this conversation yet.");

  const box = screen.getByLabelText("Ask UFO") as HTMLTextAreaElement;
  await userEvent.type(box, "first{Shift>}{Enter}{/Shift}second");
  expect(box.value).toBe("first\nsecond");
  expect(posted).toEqual([]);

  await userEvent.type(box, "{Enter}");
  await waitFor(() => expect(posted).toEqual(["first\nsecond"]));
  expect(box.value).toBe("");
  expect(screen.getByText("first second").className).toContain("whitespace-pre-wrap");
});

test("the message box is as tall as the message and stops growing at the fold", async () => {
  wire(transcript());
  open();
  await screen.findByText("No messages in this conversation yet.");

  const box = screen.getByLabelText("Ask UFO") as HTMLTextAreaElement;
  const mirror = box.previousElementSibling!;
  expect(mirror.textContent).toBe(" ");

  await userEvent.type(box, "one{Shift>}{Enter}{/Shift}two");
  expect(mirror.textContent).toBe("one\ntwo ");
  expect(mirror.className).toContain("whitespace-pre-wrap");
  expect(mirror.className).toContain("max-h-(--size-composer)");
  expect(box.className).toContain("overflow-y-auto");
});

test("the composer is dead until the conversation loads, and open through the turn it sends", async () => {
  let load: (payload: unknown) => void = () => {};
  const held = new Promise<Response>((resolve) => {
    load = (payload) => resolve(json(payload));
  });
  wire({
    ...transcript(),
    "/transcript": () => held,
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }),
  });
  open();

  const send = await screen.findByRole("button", { name: "Send" });
  expect((send as HTMLButtonElement).disabled).toBe(true);

  load({ messages: [] });
  await screen.findByText("No messages in this conversation yet.");
  expect((send as HTMLButtonElement).disabled).toBe(false);

  await userEvent.type(screen.getByLabelText("Ask UFO"), "hi");
  await userEvent.click(send);
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  expect((send as HTMLButtonElement).disabled).toBe(false);

  StreamFake.last().emit("terminal", {
    status: "done",
    model: "opus",
    tokens: 1,
    cost_micro_usd: 0,
  });
  await waitFor(() => expect((send as HTMLButtonElement).disabled).toBe(false));
});

test("a failed post states the error instead of opening a stream", async () => {
  wire({
    ...transcript(),
    "/chat": () => new Response("nope", { status: 500 }),
  });
  open();
  await screen.findByText("No messages in this conversation yet.");
  await userEvent.type(screen.getByLabelText("Ask UFO"), "hi");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  expect(await screen.findByText("Error 500 — try again.")).toBeTruthy();
  expect(StreamFake.opened.length).toBe(0);
});

const ASKING = (entry: Record<string, unknown>, title = "Pick one") => ({
  role: "assistant",
  text: "asking",
  question: { turn_id: TURN_ID, title, questions: [entry] },
});

const ASKED = { question: "Which?", options: [{ label: "left" }, { label: "right" }] };

test("a question submits its choice once, and says what is missing until there is one", async () => {
  const posts: RequestInit[] = [];
  wire({
    ...transcript({ messages: [ASKING(ASKED)] }),
    "/chat": (_url, init) => {
      posts.push(init ?? {});
      return json({ turn_id: REFUSED_TURN, body: "left" });
    },
  });
  open();

  const submit = await screen.findByRole("button", { name: "Continue" });
  await userEvent.click(submit);
  expect(posts.length).toBe(0);
  const refusal = await screen.findByRole("alert");
  expect(refusal.textContent).toBe("Choose an answer or skip this question.");
  expect(refusal.getAttribute("data-slot")).toBe("questionnaire-error");

  const left = screen.getByRole("radio", { name: "left" }) as HTMLInputElement;
  await userEvent.click(left);
  expect(left.checked).toBe(true);
  expect(posts.length).toBe(0);
  expect(screen.queryByRole("alert")).toBeNull();

  await userEvent.click(submit);

  await waitFor(() => expect(posts.length).toBe(1));
  const headers = posts[0].headers as Record<string, string>;
  expect(headers["x-ufo-answer-turn"]).toBe(TURN_ID);
  expect(headers["x-ufo-answer-question"]).toBe("0");
  expect(headers["x-ufo-timezone"]).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone);
  expect(posts[0].body).toBe("left");
  await waitFor(() => expect(screen.queryByRole("radio", { name: "right" })).toBeNull());
});

test("a pressed suggestion counts the press on the answer it sends", async () => {
  const posts: RequestInit[] = [];
  wire({
    ...transcript({ messages: [ASKING(ASKED)] }),
    "/chat": (_url, init) => {
      posts.push(init ?? {});
      return json({ turn_id: REFUSED_TURN, body: "left" });
    },
  });
  open();

  await userEvent.click(await screen.findByRole("radio", { name: "left" }));
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));

  await waitFor(() => expect(posts.length).toBe(1));
  expect((posts[0].headers as Record<string, string>)["x-ufo-click"]).toBe("thread-followup");
});

test("a typed answer counts no press", async () => {
  const posts: RequestInit[] = [];
  wire({
    ...transcript({ messages: [ASKING(ASKED)] }),
    "/chat": (_url, init) => {
      posts.push(init ?? {});
      return json({ turn_id: REFUSED_TURN, body: "neither" });
    },
  });
  open();

  await userEvent.type(await screen.findByLabelText("Which?"), "neither");
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));

  await waitFor(() => expect(posts.length).toBe(1));
  expect((posts[0].headers as Record<string, string>)["x-ufo-click"]).toBeUndefined();
});

test("a posted message reports the browser's timezone", async () => {
  const posts: RequestInit[] = [];
  wire({
    ...transcript(),
    "/chat": (_url, init) => {
      posts.push(init ?? {});
      return json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" });
    },
  });
  open();

  await screen.findByText("No messages in this conversation yet.");
  await userEvent.type(screen.getByLabelText("Ask UFO"), "hello");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  await waitFor(() => expect(posts.length).toBe(1));
  const headers = posts[0].headers as Record<string, string>;
  expect(headers["x-ufo-timezone"]).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone);
});

test("a multi-select question sends every label it holds in one answer", async () => {
  const posts: RequestInit[] = [];
  wire({
    ...transcript({
      messages: [
        ASKING(
          {
            question: "Which calendars?",
            options: [{ label: "Work" }, { label: "Home" }, { label: "Team" }],
            multi_select: true,
          },
          "Pick any",
        ),
      ],
    }),
    "/chat": (_url, init) => {
      posts.push(init ?? {});
      return json({ turn_id: REFUSED_TURN, body: "Work, Home" });
    },
  });
  open();

  await userEvent.click(await screen.findByRole("checkbox", { name: "Work" }));
  await userEvent.click(screen.getByRole("checkbox", { name: "Team" }));
  await userEvent.click(screen.getByRole("checkbox", { name: "Home" }));
  await userEvent.click(screen.getByRole("checkbox", { name: "Team" }));
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));

  await waitFor(() => expect(posts.length).toBe(1));
  expect(posts[0].body).toBe("Work, Home");
});

test("a form holding two answers delivers both, each against its entry", async () => {
  const posts: RequestInit[] = [];
  wire({
    ...transcript({
      messages: [
        {
          role: "assistant",
          text: "asking",
          question: {
            turn_id: TURN_ID,
            title: "Two things",
            questions: [
              { question: "First?", options: [{ label: "alpha" }, { label: "beta" }] },
              { question: "Second?", options: [{ label: "gamma" }, { label: "delta" }] },
            ],
          },
        },
      ],
    }),
    "/chat": (_url, init) => {
      posts.push(init ?? {});
      return posts.length === 1
        ? json({ turn_id: REFUSED_TURN, opened_run: true, body: "alpha · First?" })
        : json({
            turn_id: REFUSED_TURN,
            opened_run: false,
            arrival_id: ARRIVAL_ID,
            body: "gamma · Second?",
          });
    },
  });
  open();

  await userEvent.click(await screen.findByRole("radio", { name: "alpha" }));
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  await userEvent.click(await screen.findByRole("radio", { name: "gamma" }));
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));

  await waitFor(() => expect(posts.length).toBe(2));
  const first = posts[0].headers as Record<string, string>;
  expect(first["x-ufo-answer-turn"]).toBe(TURN_ID);
  expect(first["x-ufo-answer-question"]).toBe("0");
  expect(posts[0].body).toBe("alpha · First?");
  const second = posts[1].headers as Record<string, string>;
  expect(second["x-ufo-answer-turn"]).toBe(TURN_ID);
  expect(second["x-ufo-answer-question"]).toBe("1");
  expect(posts[1].body).toBe("gamma · Second?");

  await waitFor(() => expect(screen.queryByRole("radio", { name: "gamma" })).toBeNull());
  const reply = screen.getByText("asking").closest("[data-slot=message]") as HTMLElement;
  expect(within(reply).getByText("First?")).toBeTruthy();
  expect(within(reply).getByText("alpha")).toBeTruthy();
  expect(within(reply).getByText("Second?")).toBeTruthy();
  expect(within(reply).getByText("gamma")).toBeTruthy();
  expect(StreamFake.opened.length).toBe(1);
});

test("an entry the form cannot hold stands as prose and gates nothing", async () => {
  const posts: RequestInit[] = [];
  wire({
    ...transcript({
      messages: [
        {
          role: "assistant",
          text: "asking",
          question: {
            turn_id: TURN_ID,
            title: "Two things",
            questions: [
              { question: "Which?", options: [{ label: "left" }, { label: "right" }] },
              { question: "Send the file?", allow_attachments: true },
            ],
          },
        },
      ],
    }),
    "/chat": (_url, init) => {
      posts.push(init ?? {});
      return json({ turn_id: REFUSED_TURN, opened_run: true, body: "left · Which?" });
    },
  });
  open();

  await screen.findByRole("radio", { name: "left" });
  expect(screen.getByText("Answer in the message box below.")).toBeTruthy();
  await userEvent.click(screen.getByRole("radio", { name: "left" }));
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));

  await waitFor(() => expect(posts.length).toBe(1));
  const headers = posts[0].headers as Record<string, string>;
  expect(headers["x-ufo-answer-question"]).toBe("0");
  expect(posts[0].body).toBe("left · Which?");
});

test("a lone question the form cannot hold says where the answer goes, with no controls", async () => {
  wire(transcript({ messages: [ASKING({ question: "Send the file?", allow_attachments: true })] }));
  open();

  await screen.findByText("Send the file?");
  expect(screen.getByText("Answer in the message box below.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Continue" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Skip" })).toBeNull();
});

test("an answered question states the words the surface confirmed it admitted", async () => {
  wire({
    ...transcript({ messages: [ASKING(ASKED)] }),
    "/chat": (_url, init) => json({ turn_id: REFUSED_TURN, body: String(init?.body ?? "") }),
  });
  open();

  await userEvent.click(await screen.findByRole("radio", { name: "left" }));
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));

  await waitFor(() => expect(screen.queryByRole("radio", { name: "left" })).toBeNull());
  const reply = screen.getByText("asking").closest("[data-slot=message]") as HTMLElement;
  expect(within(reply).getByText("Which?")).toBeTruthy();
  expect(within(reply).getByText("left")).toBeTruthy();
});

test("an answer the card states says nothing a second time as a message", async () => {
  wire({
    ...transcript({
      messages: [
        {
          role: "assistant",
          text: "asking",
          question: {
            turn_id: TURN_ID,
            title: "Two things",
            questions: [
              { question: "First?", options: [{ label: "alpha" }, { label: "beta" }] },
              { question: "Second?", options: [{ label: "gamma" }, { label: "delta" }] },
            ],
          },
        },
      ],
    }),
    "/chat": (_url, init) => json({ turn_id: REFUSED_TURN, body: String(init?.body ?? "") }),
  });
  open();

  await userEvent.click(await screen.findByRole("radio", { name: "alpha" }));
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  await userEvent.click(await screen.findByRole("radio", { name: "gamma" }));
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));

  await waitFor(() => expect(screen.queryByRole("radio", { name: "gamma" })).toBeNull());
  const reply = screen.getByText("asking").closest("[data-slot=message]") as HTMLElement;
  expect(within(reply).getByText("alpha")).toBeTruthy();
  expect(within(reply).getByText("gamma")).toBeTruthy();
  expect(screen.queryByText(saying("alpha · First?"))).toBeNull();
  expect(screen.queryByText(saying("gamma · Second?"))).toBeNull();
});

test("a question the conversation has moved past states its answers and offers none", async () => {
  wire(
    transcript({
      messages: [
        {
          role: "assistant",
          text: "asking",
          question: {
            turn_id: TURN_ID,
            title: "Two things",
            questions: [
              { question: "First?", options: [{ label: "alpha" }, { label: "beta" }] },
              { question: "Second?", options: [{ label: "gamma" }, { label: "delta" }] },
            ],
            answered: { 0: "alpha · First?" },
            closed: true,
          },
        },
      ],
    }),
  );
  open();

  const reply = (await screen.findByText("asking")).closest("[data-slot=message]") as HTMLElement;
  expect(within(reply).getByText("First?")).toBeTruthy();
  expect(within(reply).getByText("alpha")).toBeTruthy();
  expect(screen.queryByText("Second?")).toBeNull();
  expect(screen.queryByRole("radio", { name: "gamma" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Continue" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Skip" })).toBeNull();
});

test("a question stands under the reply that asked it, not at the foot of the log", async () => {
  wire(transcript({ messages: [ASKING(ASKED), { role: "user", text: "one moment" }] }));
  open();

  const left = await screen.findByRole("radio", { name: "left" });
  const reply = screen.getByText("asking").closest("[data-slot=message]") as HTMLElement;
  expect(reply.contains(left)).toBe(true);
  const rows = screen.getByTestId("log").querySelectorAll("[data-slot=message-scroller-item]");
  expect(rows[rows.length - 1].textContent).toBe("one moment");
});

test("a settled turn names each shared file once, with its size", async () => {
  wire({ ...transcript(), "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }) });
  open();
  await screen.findByText("No messages in this conversation yet.");
  await userEvent.type(screen.getByLabelText("Ask UFO"), "hi");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));

  StreamFake.last().emit("message", { text: "Here it is." });
  StreamFake.last().emit("files", {
    files: [
      {
        filename: "report.csv",
        url: "/dl/report.csv",
        size_bytes: 2048,
        preview_url: null,
        media_type: "text/csv",
      },
    ],
  });
  StreamFake.last().emit("terminal", {
    status: "done",
    model: "opus",
    tokens: 9,
    cost_micro_usd: 1_000_000,
  });

  expect(await screen.findByText(saying("Here it is."))).toBeTruthy();
  const files = screen.getAllByRole("button", { name: "report.csv" });
  expect(files).toHaveLength(1);
  expect(screen.getByText("2 kB")).toBeTruthy();

  await userEvent.click(files[0]);
  const sheet = await screen.findByRole("dialog", { name: "report.csv" });
  expect(within(sheet).getByRole("link", { name: "Download" }).getAttribute("href")).toBe(
    "/dl/report.csv",
  );
});

test("a turn puts shared images in one carousel before its document grid", async () => {
  wire(
    transcript({
      messages: [
        {
          role: "assistant",
          text: "Here they are.",
          files: [
            {
              filename: "report.pdf",
              url: "/dl/report.pdf",
              size_bytes: 2048,
              preview_url: "https://web/artifacts/preview/report.pdf?token=signed",
              media_type: "application/pdf",
            },
            {
              filename: "portrait.jpg",
              url: "/dl/portrait.jpg",
              size_bytes: 88_000,
              preview_url: "https://web/artifacts/preview/portrait.jpg?token=signed",
              media_type: "image/jpeg",
            },
            {
              filename: "scan.bmp",
              url: "/dl/scan.bmp",
              size_bytes: 4096,
              preview_url: null,
              media_type: "image/bmp",
            },
            {
              filename: "notes.md",
              url: "/dl/notes.md",
              size_bytes: 512,
              preview_url: null,
              media_type: "text/markdown",
            },
            {
              filename: "chart.png",
              url: "/dl/chart.png",
              size_bytes: 16_000,
              preview_url: "https://web/artifacts/preview/chart.png?token=signed",
              media_type: "image/png",
            },
          ],
        },
      ],
    }),
  );
  open();

  const reply = (await screen.findByText(saying("Here they are."))).closest(
    "[data-slot=message]",
  ) as HTMLElement;
  const carousel = reply.querySelector('[data-slot="attachment-group"]') as HTMLElement;
  const documents = reply.querySelector('[data-slot="attachment-grid"]') as HTMLElement;
  const portrait = within(carousel).getByRole("img", { name: "portrait.jpg" });
  const chart = within(carousel).getByRole("img", { name: "chart.png" });
  const reportPreview = within(documents).getByRole("img", { name: "report.pdf" });
  const report = within(documents).getByRole("button", { name: "report.pdf" });
  const scan = within(documents).getByRole("button", { name: "scan.bmp" });
  const notes = within(documents).getByRole("button", { name: "notes.md" });
  expect(carousel.children).toHaveLength(2);
  expect(carousel.children[0].contains(portrait)).toBe(true);
  expect(carousel.children[1].contains(chart)).toBe(true);
  expect(documents.children).toHaveLength(3);
  expect(documents.children[0].contains(reportPreview)).toBe(true);
  expect(documents.children[0].contains(report)).toBe(true);
  expect(documents.children[1].contains(scan)).toBe(true);
  expect(documents.children[2].contains(notes)).toBe(true);
  expect(reportPreview.className).toContain("top-0");
  expect(reportPreview.className).toContain("h-auto w-full");
  expect(reportPreview.className).not.toContain("object-cover");
  expect(portrait.className).not.toContain("top-0");
  expect(carousel.nextElementSibling).toBe(documents);
});

test("a file the running turn shares stands under the log before the turn ends", async () => {
  wire({ ...transcript(), "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }) });
  open();
  await screen.findByText("No messages in this conversation yet.");
  await userEvent.type(screen.getByLabelText("Ask UFO"), "hi");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));

  StreamFake.last().emit("message", { text: "Here it is." });
  StreamFake.last().emit("files", {
    files: [
      {
        filename: "report.csv",
        url: "/dl/report.csv",
        size_bytes: 2048,
        preview_url: null,
        media_type: "text/csv",
      },
    ],
  });

  expect(await screen.findByRole("button", { name: "report.csv" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy();

  StreamFake.last().emit("terminal", {
    status: "done",
    model: "opus",
    tokens: 9,
    cost_micro_usd: 1_000_000,
  });
  await waitFor(() => expect(screen.getByRole("button", { name: "Send" })).toBeTruthy());
  expect(screen.getAllByRole("button", { name: "report.csv" })).toHaveLength(1);
});

test("a file stays on the reply that shared it when a follow-up opens the next turn", async () => {
  let land: (payload: unknown) => void = () => {};
  const admitted = new Promise<Response>((resolve) => {
    land = (payload) => resolve(json(payload));
  });
  let sends = 0;
  wire({
    ...transcript(),
    "/chat": () => {
      sends += 1;
      return sends === 1
        ? Promise.resolve(json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }))
        : admitted;
    },
  });
  open();
  await screen.findByText("No messages in this conversation yet.");
  await userEvent.type(screen.getByLabelText("Ask UFO"), "share it");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));

  StreamFake.last().emit("message", { text: "Here it is." });
  StreamFake.last().emit("files", {
    files: [
      {
        filename: "portrait.jpg",
        url: "/dl/portrait.jpg",
        size_bytes: 88_000,
        preview_url: "https://web/artifacts/preview/portrait.jpg?token=signed",
        media_type: "image/jpeg",
      },
      {
        filename: "report.csv",
        url: "/dl/report.csv",
        size_bytes: 2048,
        preview_url: null,
        media_type: "text/csv",
      },
    ],
  });
  StreamFake.last().emit("terminal", {
    status: "done",
    model: "opus",
    tokens: 9,
    cost_micro_usd: 1_000_000,
  });
  expect(await screen.findByRole("button", { name: "report.csv" })).toBeTruthy();
  expect(screen.getByRole("img", { name: "portrait.jpg" })).toBeTruthy();

  await userEvent.type(screen.getByLabelText("Ask UFO"), "and another");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  expect(await screen.findByText(saying("and another"))).toBeTruthy();
  const reply = screen.getByText(saying("Here it is.")).closest("[data-slot=message]") as HTMLElement;
  expect(within(reply).getByRole("button", { name: "report.csv" })).toBeTruthy();
  expect(within(reply).getByRole("img", { name: "portrait.jpg" })).toBeTruthy();

  land({ turn_id: "turn-2", conversation_id: CONVO_ID, title: "hello" });
  await waitFor(() => expect(StreamFake.opened.length).toBe(2));
  expect(screen.getAllByRole("button", { name: "report.csv" })).toHaveLength(1);
  expect(screen.getAllByRole("img", { name: "portrait.jpg" })).toHaveLength(1);
});

test("a write-up the reply carried opens as the detailed report above the attachments", async () => {
  wire(
    transcript({
      messages: [
        {
          role: "assistant",
          text: "Move the event-driven jobs onto a queue.",
          files: [
            {
              filename: "nightly-runner-queue.md",
              url: "/dl/nightly-runner-queue.md",
              size_bytes: 2048,
              preview_url: null,
              media_type: "text/markdown",
              role: "details",
            },
            {
              filename: "queue-costs.md",
              url: "/dl/queue-costs.md",
              size_bytes: 512,
              preview_url: null,
              media_type: "text/markdown",
              role: "details",
              subject: "Open the cost table",
            },
            {
              filename: "report.csv",
              url: "/dl/report.csv",
              size_bytes: 2048,
              preview_url: null,
              media_type: "text/csv",
            },
          ],
        },
      ],
    }),
  );
  open();

  expect(await screen.findByText(saying("Move the event-driven jobs onto a queue."))).toBeTruthy();
  const opener = screen.getByRole("button", { name: "Open detailed report" });
  const captioned = screen.getByRole("button", { name: "Open the cost table" });
  const card = screen.getByRole("button", { name: "report.csv" });
  expect(opener.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(captioned.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.queryByRole("button", { name: "nightly-runner-queue.md" })).toBeNull();
  expect(screen.queryByRole("button", { name: "queue-costs.md" })).toBeNull();

  await userEvent.click(opener);
  const sheet = await screen.findByRole("dialog", { name: "nightly-runner-queue.md" });
  expect(within(sheet).getByRole("link", { name: "Download" }).getAttribute("href")).toBe(
    "/dl/nightly-runner-queue.md",
  );
});

test("a chat address naming a report opens that write-up on arrival", async () => {
  const REPORT = "77777777-7777-4777-8777-777777777777";
  wire(
    transcript({
      messages: [
        {
          role: "assistant",
          text: "Move the event-driven jobs onto a queue.",
          files: [
            {
              id: REPORT,
              filename: "nightly-runner-queue.md",
              url: "/dl/nightly-runner-queue.md",
              size_bytes: 2048,
              preview_url: null,
              media_type: "text/markdown",
              role: "details",
            },
          ],
        },
      ],
    }),
  );
  location.hash = chatHash(CONVO_ID, undefined, REPORT);
  open();

  const sheet = await screen.findByRole("dialog", { name: "nightly-runner-queue.md" });
  expect(within(sheet).getByRole("link", { name: "Download" }).getAttribute("href")).toBe(
    "/dl/nightly-runner-queue.md",
  );
});

test("a reloaded conversation draws files on the earlier reply that shared them", async () => {
  wire(
    transcript({
      messages: [
        { role: "user", text: "find a headshot" },
        {
          role: "assistant",
          text: "Here is the portrait.",
          files: [
            {
              filename: "portrait.jpg",
              url: "/dl/portrait.jpg",
              size_bytes: 88_000,
              preview_url: "https://web/artifacts/preview/portrait.jpg?token=signed",
              media_type: "image/jpeg",
            },
            {
              filename: "report.csv",
              url: "/dl/report.csv",
              size_bytes: 2048,
              preview_url: null,
              media_type: "text/csv",
            },
          ],
        },
        { role: "user", text: "thanks" },
        { role: "assistant", text: "Anything else?" },
      ],
    }),
  );
  open();

  const picture = await screen.findByRole("img", { name: "portrait.jpg" });
  const reply = screen.getByText(saying("Here is the portrait.")).closest(
    "[data-slot=message]",
  ) as HTMLElement;
  expect(reply.contains(picture)).toBe(true);
  expect(within(reply).getByRole("button", { name: "report.csv" })).toBeTruthy();
  const rows = screen.getByTestId("log").querySelectorAll("[data-slot=message-scroller-item]");
  expect(rows[rows.length - 1].textContent).toBe("Anything else?");
});

const CREATED_APP = { id: SECOND_ID, name: "second", model: "claude-sonnet-5", icon: "aten" };

test("an application the turn created stands on the reply that made it, and opens it", async () => {
  wire({
    ...transcript(),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }),
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);
  await screen.findByText("No messages in this conversation yet.");
  await userEvent.type(screen.getByLabelText("Ask UFO"), "build me a digest");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));

  StreamFake.last().emit("message", { text: "Second is set up." });
  StreamFake.last().emit("apps", { apps: [CREATED_APP] });
  StreamFake.last().emit("terminal", {
    status: "done",
    model: "opus",
    tokens: 9,
    cost_micro_usd: 1_000_000,
  });

  const reply = (await screen.findByText(saying("Second is set up."))).closest(
    "[data-slot=message]",
  ) as HTMLElement;
  const card = within(reply).getByRole("link", { name: /Second/ });
  expect(card.getAttribute("href")).toBe("#/agents/" + SECOND_ID);
  expect(card.textContent).toContain("claude-sonnet-5");

  await userEvent.click(card);
  expect(location.hash).toBe("#/agents/" + SECOND_ID);
  expect(await screen.findByRole("region", { name: "Second" })).toBeTruthy();
});

test("a reloaded conversation draws the app card on the reply that created it", async () => {
  wire(
    transcript({
      messages: [
        { role: "user", text: "build me a digest" },
        { role: "assistant", text: "Second is set up.", apps: [CREATED_APP] },
        { role: "user", text: "thanks" },
        { role: "assistant", text: "Anything else?" },
      ],
    }),
  );
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  const reply = (await screen.findByText(saying("Second is set up."))).closest(
    "[data-slot=message]",
  ) as HTMLElement;
  expect(within(reply).getByRole("link", { name: /Second/ }).getAttribute("href")).toBe(
    "#/agents/" + SECOND_ID,
  );
  const rows = screen.getByTestId("log").querySelectorAll("[data-slot=message-scroller-item]");
  expect(rows[rows.length - 1].textContent).toBe("Anything else?");
});

test("an image the turn shares stands inline in the answer and opens the artifacts sidebar", async () => {
  wire({
    ["/conversations/" + CONVO_ID + "/slots/artifacts"]: () =>
      json({
        type: "artifacts",
        artifacts: [
          {
            filename: "portrait.jpg",
            subject: null,
            media_type: "image/jpeg",
            size_bytes: 88_000,
            created_at: "2026-08-15T12:00:00Z",
            url: "/dl/portrait.jpg",
            preview: {
              type: "image",
              media_type: "image/jpeg",
              url: "https://web/artifacts/preview/portrait.jpg?token=signed",
            },
          },
        ],
        truncated: false,
      }),
    ...transcript(),
    "/slots": () =>
      json({
        slots: [
          { id: "artifacts", label: "Artifacts", icon: "artifact", kind: "artifacts", count: 1 },
        ],
      }),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }),
  });
  open();
  await screen.findByText("No messages in this conversation yet.");
  await userEvent.type(screen.getByLabelText("Ask UFO"), "find a headshot");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));

  StreamFake.last().emit("message", { text: "Here is the portrait." });
  StreamFake.last().emit("files", {
    files: [
      {
        filename: "portrait.jpg",
        url: "/dl/portrait.jpg",
        size_bytes: 88_000,
        preview_url: "https://web/artifacts/preview/portrait.jpg?token=signed",
        media_type: "image/jpeg",
      },
      {
        filename: "report.csv",
        url: "/dl/report.csv",
        size_bytes: 2048,
        preview_url: null,
        media_type: "text/csv",
      },
    ],
  });
  StreamFake.last().emit("terminal", {
    status: "done",
    model: "opus",
    tokens: 9,
    cost_micro_usd: 1_000_000,
  });

  const picture = await screen.findByRole("img", { name: "portrait.jpg" });
  expect(picture.getAttribute("src")).toBe("https://web/artifacts/preview/portrait.jpg?token=signed");
  expect(screen.queryByRole("link", { name: "portrait.jpg" })).toBeNull();
  expect(screen.getByRole("button", { name: "report.csv" })).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "portrait.jpg" }));
  expect(location.hash).toBe("#/c/" + CONVO_ID);
  const sheet = await screen.findByRole("dialog", { name: "portrait.jpg" });
  expect(within(sheet).getByRole("link", { name: "Download" }).getAttribute("href")).toBe(
    "/dl/portrait.jpg",
  );
});

test("every file the turn shares opens in the attachment sheet instead of downloading", async () => {
  wire({
    ["/conversations/" + CONVO_ID + "/slots/artifacts"]: () =>
      json({
        type: "artifacts",
        artifacts: [
          {
            filename: "notes.md",
            subject: null,
            media_type: "text/markdown",
            size_bytes: 512,
            created_at: "2026-08-16T12:00:00Z",
            url: "/dl/notes.md",
            preview: null,
          },
        ],
        truncated: false,
      }),
    "/dl/notes.md": () => new Response("# Notes"),
    ...transcript(),
    "/slots": () =>
      json({
        slots: [
          { id: "artifacts", label: "Artifacts", icon: "artifact", kind: "artifacts", count: 1 },
        ],
      }),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }),
  });
  open();
  await screen.findByText("No messages in this conversation yet.");
  await userEvent.type(screen.getByLabelText("Ask UFO"), "write the notes");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));

  StreamFake.last().emit("message", { text: "Here are the notes." });
  StreamFake.last().emit("files", {
    files: [
      {
        filename: "notes.md",
        url: "/dl/notes.md",
        size_bytes: 512,
        preview_url: null,
        media_type: "text/markdown",
      },
      {
        filename: "report.csv",
        url: "/dl/report.csv",
        size_bytes: 2048,
        preview_url: null,
        media_type: "text/csv",
      },
    ],
  });
  StreamFake.last().emit("terminal", {
    status: "done",
    model: "opus",
    tokens: 9,
    cost_micro_usd: 1_000_000,
  });

  expect(await screen.findByRole("button", { name: "notes.md" })).toBeTruthy();
  expect(screen.queryByRole("link", { name: "notes.md" })).toBeNull();
  expect(screen.getByRole("button", { name: "report.csv" })).toBeTruthy();
  expect(screen.queryByRole("link", { name: "report.csv" })).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "notes.md" }));
  expect(location.hash).toBe("#/c/" + CONVO_ID);
  const sheet = await screen.findByRole("dialog", { name: "notes.md" });
  expect(await within(sheet).findByRole("heading", { name: "Notes" })).toBeTruthy();
  expect(within(sheet).getByRole("link", { name: "Download" }).getAttribute("href")).toBe(
    "/dl/notes.md",
  );
  await userEvent.click(within(sheet).getByRole("button", { name: "Close" }));

  cleanup();
  render(
    <ConversationTranscript
      title="Review PR 1268"
      messages={[
        {
          role: "assistant",
          text: "Here are the notes.",
          files: [
            {
              filename: "notes.md",
              url: "/dl/notes.md",
              size_bytes: 512,
              preview_url: null,
              media_type: "text/markdown",
            },
          ],
        },
      ]}
    />,
  );
  expect(screen.getByRole("button", { name: "notes.md" })).toBeTruthy();
  expect(screen.queryByRole("link", { name: "notes.md" })).toBeNull();
});

test("a credential handoff stores a value and drops the prompt it answered", async () => {
  wire({
    ...transcript({
      messages: [{ role: "assistant", text: "need a key" }],
      credentials: {
        sealed: "sealed-token",
        reason: "notion authenticates with this value.",
        prompts: [{ slot: "notion_token", prompt: "Notion token" }],
      },
    }),
    "/credentials": () => new Response("stored"),
  });
  open();

  expect(await screen.findByText("notion authenticates with this value.")).toBeTruthy();
  await userEvent.type(screen.getByPlaceholderText("notion_token"), "secret");
  await userEvent.click(screen.getByRole("button", { name: "Store" }));

  expect(await screen.findByText("Stored notion_token.")).toBeTruthy();
  expect(screen.queryByPlaceholderText("notion_token")).toBeNull();
});

test("an MCP credential handoff saves one server update from named fields", async () => {
  const submitted: { current: URLSearchParams | null } = { current: null };
  wire({
    ...transcript({
      messages: [{ role: "assistant", text: "configure it" }],
      credentials: {
        sealed: "sealed-token",
        reason: "Paste the whole JSON value.",
        prompts: [{ slot: "mcp_servers", prompt: "MCP server JSON" }],
      },
    }),
    "/credentials": (_url, init) => {
      submitted.current = init?.body as URLSearchParams;
      return new Response("stored");
    },
  });
  open();

  expect(
    await screen.findByText("Add or update one MCP server. Saved servers stay in place."),
  ).toBeTruthy();
  expect(screen.queryByText("Paste the whole JSON value.")).toBeNull();
  await userEvent.type(screen.getByLabelText("Server name"), "vercel");
  await userEvent.type(screen.getByLabelText("Server URL"), "https://mcp.vercel.com");
  await userEvent.type(screen.getByLabelText("Access token"), "vercel-token");
  await userEvent.click(screen.getByRole("button", { name: "Save server" }));

  expect(await screen.findByText("MCP server saved.")).toBeTruthy();
  expect(submitted.current?.get("sealed")).toBe("sealed-token");
  expect(submitted.current?.get("slot")).toBe("mcp_servers");
  expect(JSON.parse(submitted.current?.get("value") ?? "")).toEqual({
    name: "vercel",
    url: "https://mcp.vercel.com",
    auth: "vercel-token",
  });
});

test("an MCP credential handoff removes one named server", async () => {
  const submitted: { current: URLSearchParams | null } = { current: null };
  wire({
    ...transcript({
      messages: [{ role: "assistant", text: "remove it" }],
      credentials: {
        sealed: "sealed-token",
        reason: "Update the MCP servers.",
        prompts: [{ slot: "mcp_servers", prompt: "MCP server JSON" }],
      },
    }),
    "/credentials": (_url, init) => {
      submitted.current = init?.body as URLSearchParams;
      return new Response("stored");
    },
  });
  open();

  await userEvent.type(await screen.findByLabelText("Server name"), "vercel");
  await userEvent.click(screen.getByRole("button", { name: "Remove server" }));
  await userEvent.click(screen.getByRole("button", { name: "Confirm remove server" }));

  expect(await screen.findByText("MCP server removed.")).toBeTruthy();
  expect(JSON.parse(submitted.current?.get("value") ?? "")).toEqual({
    name: "vercel",
    remove: true,
  });
});

test("a streamed chunk never steals focus from where the member put it", async () => {
  wire({ ...transcript(), "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }) });
  open();
  await screen.findByText("No messages in this conversation yet.");
  await userEvent.type(screen.getByLabelText("Ask UFO"), "hi");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));

  const elsewhere = within(screen.getByRole("navigation", { name: "Tabs" })).getByRole("button", {
    name: "Workspace",
  });
  elsewhere.focus();
  StreamFake.last().emit("message", { text: "chunk" });
  await screen.findByText(saying("chunk"));
  expect(document.activeElement).toBe(elsewhere);
});

/** Each page's response names the one above it, so the chain is the server's to state. The test's
 *  IntersectionObserver sees everything at once, so the pages land unprompted. */
test("a compacted conversation pages its earlier messages in above the tail", async () => {
  const { calls } = wire({
    "/transcript?cursor=page-2": () =>
      json({
        messages: [
          { role: "user", text: "second ask" },
          { role: "assistant", text: "second reply" },
        ],
        earlier_cursor: "page-1",
      }),
    "/transcript?cursor=page-1": () =>
      json({
        messages: [
          { role: "user", text: "first ask" },
          { role: "assistant", text: "first reply" },
        ],
      }),
    ...transcript({
      messages: [
        { role: "user", text: "third ask" },
        { role: "assistant", text: "third reply" },
      ],
      earlier_cursor: "page-2",
    }),
  });
  open();

  expect(await screen.findByText("first reply")).toBeTruthy();
  const log = screen.getByTestId("log").textContent ?? "";
  expect(log.indexOf("first ask")).toBeGreaterThanOrEqual(0);
  expect(log.indexOf("first ask")).toBeLessThan(log.indexOf("second ask"));
  expect(log.indexOf("second ask")).toBeLessThan(log.indexOf("third ask"));
  expect(screen.queryByText("Loading earlier messages…")).toBeNull();
  expect(calls.filter((url) => url.includes("/transcript?cursor="))).toEqual([
    "/surface/web/agents/" +
      AGENT.id +
      "/conversations/" +
      CONVO_ID +
      "/transcript?cursor=page-2",
    "/surface/web/agents/" +
      AGENT.id +
      "/conversations/" +
      CONVO_ID +
      "/transcript?cursor=page-1",
  ]);
});

test("the reply that landed keeps the streaming row's element", async () => {
  wire(transcript({ messages: [{ role: "user", text: "Review PR 1268." }], turn: TURN_ID }));
  open();
  await screen.findByText("Review PR 1268.");
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("message", { text: "landed words" });
  const streamed = await screen.findByText(saying("landed words"));
  const row = streamed.closest("[data-slot=message-scroller-item]");
  expect(row).toBeTruthy();
  StreamFake.last().emit("terminal", {
    status: "done",
    model: "opus",
    tokens: 9,
    cost_micro_usd: 1_000_000,
  });
  await waitFor(() => expect(screen.getByRole("log").getAttribute("aria-busy")).toBe("false"));
  const settled = screen
    .getByText(saying("landed words"))
    .closest("[data-slot=message-scroller-item]");
  expect(settled).toBe(row);
});

test("a conversation that never compacted asks for no pages", async () => {
  const { calls } = wire(transcript({ messages: [{ role: "assistant", text: "Done." }] }));
  open();
  await screen.findByText("Done.");
  expect(calls.filter((url) => url.includes("/transcript?cursor="))).toEqual([]);
});

test("a message states when it landed on its meta line, and in full under the pointer", () => {
  render(
    <TranscriptScroll>
      <MessageLog
        messages={[
          { role: "user", text: "what time", at: "2026-08-01T09:05:00Z" },
          { role: "assistant", text: "just gone nine" },
        ]}
      />
    </TranscriptScroll>,
  );

  const stamped = screen.getByText("what time").closest("[data-slot=message-scroller-item]")!;
  expect(stamped.querySelector("time")).toBeNull();
  const meta = stamped.querySelector("[data-slot=marker]")!;
  expect(meta.textContent).toBe("Aug 1, 2:35 PM");
  expect(within(meta as HTMLElement).getByLabelText("Copy message")).toBeTruthy();
  expect(stamped.querySelector("[data-slot=message-content]")?.getAttribute("title")).toBe(
    "Aug 1 2026 at 2:35 PM GMT+5:30",
  );
  const bare = screen.getByText("just gone nine").closest("[data-slot=message-scroller-item]")!;
  expect(bare.querySelector("[data-slot=marker]")).toBeNull();
  expect(bare.querySelector("[data-slot=message-content]")?.getAttribute("title")).toBeNull();
});

test("a stamp from another day names the day, and one from another year names that too", () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date("2026-08-01T09:05:00Z"));
  try {
    render(
      <TranscriptScroll>
        <MessageLog
          messages={[
            { role: "user", text: "today", at: "2026-08-01T05:32:00Z" },
            { role: "user", text: "earlier this year", at: "2026-08-30T05:32:00Z" },
            { role: "user", text: "last year", at: "2025-08-30T05:32:00Z" },
          ]}
        />
      </TranscriptScroll>,
    );

    const line = (text: string) =>
      screen
        .getByText(text)
        .closest("[data-slot=message-scroller-item]")!
        .querySelector("[data-slot=marker]")!.textContent;
    expect(line("today")).toBe("11:02 AM");
    expect(line("earlier this year")).toBe("Aug 30, 11:02 AM");
    expect(line("last year")).toBe("Aug 30 2025, 11:02 AM");
  } finally {
    vi.useRealTimers();
  }
});

/** jsdom lays nothing out, so a page landing's geometry is stated by hand: a pane showing three hundred
 *  pixels, every row a hundred tall at its index. */
test("loading a page above holds the line being read where it was", async () => {
  const load = vi.fn();
  const earlier: EarlierMessages = { pages: [], more: true, loading: false, failed: true, load };
  const log = (state: EarlierMessages) => (
    <div data-testid="pane" style={{ overflowY: "auto" }}>
      <TranscriptScroll>
        <MessageLog
          messages={[
            { role: "user", text: "third ask" },
            { role: "assistant", text: "third reply" },
          ]}
          earlier={state}
        />
      </TranscriptScroll>
    </div>
  );
  const view = render(log(earlier));
  const pane = screen.getByTestId("pane");
  const box = (top: number, height: number) =>
    ({ top, bottom: top + height, height, left: 0, right: 0, width: 0, x: 0, y: top }) as DOMRect;
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (
    this: Element,
  ) {
    if (this === pane) return box(0, 300);
    if ((this as HTMLElement).dataset?.slot !== "message-scroller-item") return box(0, 0);
    let above = 0;
    for (let held = this.previousElementSibling; held; held = held.previousElementSibling) {
      above += 1;
    }
    return box(above * 100 - pane.scrollTop, 100);
  });
  pane.scrollTop = 0;

  await userEvent.click(screen.getByText("Couldn't load earlier messages — retry"));
  expect(load).toHaveBeenCalledTimes(1);
  view.rerender(
    log({
      pages: [
        {
          cursor: "page-1",
          messages: [
            { role: "user", text: "first ask" },
            { role: "assistant", text: "first reply" },
          ],
        },
      ],
      more: false,
      loading: false,
      failed: false,
      load,
    }),
  );
  expect(pane.scrollTop).toBe(200);
});

test("a transcript read back retries a failed page only when pressed", async () => {
  const load = vi.fn();
  render(
    <ConversationTranscript
      title="Review PR 1268"
      messages={[{ role: "assistant", text: "tail reply" }]}
      earlier={{
        pages: [{ cursor: "page-1", messages: [{ role: "user", text: "first ask" }] }],
        more: true,
        loading: false,
        failed: true,
        load,
      }}
    />,
  );
  const texts = screen.getByRole("log").textContent ?? "";
  expect(texts.indexOf("first ask")).toBeLessThan(texts.indexOf("tail reply"));
  expect(load).not.toHaveBeenCalled();
  await userEvent.click(screen.getByText("Couldn't load earlier messages — retry"));
  expect(load).toHaveBeenCalledTimes(1);
});

test("the live chat scrolls its own pane and a transcript read back scrolls with the page", async () => {
  wire(transcript({ messages: [{ role: "assistant", text: "Done." }] }));
  open();
  await screen.findByText("Done.");
  const pane = screen.getByTestId("log").closest("[data-slot=message-scroller]");
  expect(pane?.className).toContain("flex-1");
  expect(screen.getByRole("button", { name: "Jump to bottom" })).toBeTruthy();

  cleanup();
  render(
    <ConversationTranscript
      title="Review PR 1268"
      messages={[{ role: "assistant", text: "Done." }]}
    />,
  );
  expect(screen.getByText("Done.")).toBeTruthy();
  expect(screen.queryByTestId("log")).toBeNull();
  expect(document.querySelector("[data-slot=message-scroller]")).toBeNull();
  expect(screen.queryByRole("button", { name: "Jump to bottom" })).toBeNull();
});

/** jsdom lays nothing out, so the pane is stated here; a browser clamps the foot to what is left under
 *  the fold. */
const FOLD = 300;
const CONTENT = 1000;
const FOOT = CONTENT - FOLD;

function laidLog(log: HTMLElement): void {
  Object.defineProperty(log, "scrollHeight", { configurable: true, value: CONTENT });
  Object.defineProperty(log, "clientHeight", { configurable: true, value: FOLD });
  const box = (top: number, height: number) =>
    ({ top, bottom: top + height, height, left: 0, right: 0, width: 0, x: 0, y: top }) as DOMRect;
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    if (this === log) return box(0, FOLD);
    const row = (this.parentElement as HTMLElement | null)?.dataset.slot;
    return row === "message-scroller-content" ? box(CONTENT - log.scrollTop - 1, 1) : box(0, 0);
  });
}

/** jsdom fires no resize, so a reply growing is stated by adding to the content the scroller watches. */
function lands(log: HTMLElement) {
  log
    .querySelector("[data-slot=message-scroller-content]")!
    .appendChild(document.createElement("p"));
}

test("streaming keeps the log pinned at the bottom but never yanks a reader back down", async () => {
  wire({ ...transcript(), "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }) });
  open();
  await screen.findByText("No messages in this conversation yet.");
  await userEvent.type(screen.getByLabelText("Ask UFO"), "hi");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));

  const log = screen.getByTestId("log");
  laidLog(log);

  log.scrollTop = FOOT;
  fireEvent.scroll(log);
  log.scrollTop = 100;
  fireEvent.scroll(log);
  const back = screen.getByRole("button", { name: "Jump to bottom" });
  expect(back.getAttribute("data-active")).toBe("true");

  StreamFake.last().emit("message", { text: "while reading" });
  await screen.findByText(saying("while reading"));
  lands(log);
  await waitFor(() => expect(back.getAttribute("data-active")).toBe("true"));
  expect(log.scrollTop).toBe(100);

  await userEvent.click(back);
  expect(log.scrollTop).toBe(FOOT);
  await waitFor(() => expect(back.getAttribute("data-active")).toBe("false"));
});

test("a dropped file is named on the composer, comes off it, and rides the message", async () => {
  const { handler } = wire({
    ...transcript(),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }),
  });
  open();
  await screen.findByText("No messages in this conversation yet.");

  const composer = document.querySelector("form[data-field-card]")!;
  const drop = (name: string) =>
    fireEvent.drop(composer, {
      dataTransfer: { types: ["Files"], files: [new File(["body"], name, { type: "text/plain" })] },
    });

  drop("notes.txt");
  drop("second.txt");
  expect(screen.getByText("notes.txt")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Remove notes.txt" }));
  expect(screen.queryByText("notes.txt")).toBeNull();
  expect(screen.getByText("second.txt")).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(screen.queryByLabelText("Attached files")).toBeNull());
  const sent = handler.mock.calls.find(([url]) => String(url).includes("/chat?"))?.[1]?.body;
  expect(sent).toBeInstanceOf(FormData);
  expect((sent as FormData).getAll("file").map((file) => (file as File).name)).toEqual([
    "second.txt",
  ]);
  expect(await screen.findByText("second.txt")).toBeTruthy();
});

test("a send the composer cannot answer leaves the attachment where the member put it", async () => {
  wire({ ...transcript(), "/chat": () => new Promise<Response>(() => {}) });
  location.hash = "#/";
  open();
  await screen.findByLabelText("Ask UFO");

  const composer = document.querySelector("form[data-field-card]")!;
  const chips = () => within(composer as HTMLElement).queryAllByRole("listitem");
  fireEvent.drop(composer, {
    dataTransfer: { types: ["Files"], files: [new File(["body"], "held.txt")] },
  });
  await waitFor(() => expect(chips().length).toBe(1));

  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(chips().length).toBe(0));

  fireEvent.drop(composer, {
    dataTransfer: { types: ["Files"], files: [new File(["body"], "second.txt")] },
  });
  await userEvent.type(screen.getByLabelText("Ask UFO"), "{Enter}");
  expect(chips().length).toBe(1);
});

test("a second press while the attachment travels sends the file once", async () => {
  let mint: () => void = () => {};
  const minting = new Promise<void>((resolve) => (mint = resolve));
  const { handler } = wire({
    ...transcript(),
    "/uploads": async () => {
      await minting;
      return json({ key: "artifacts/abc/notes.txt", put_url: "https://store/1", sig: "signed" });
    },
    "store/1": () => new Response("", { status: 200 }),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }),
  });
  open();
  await screen.findByText("No messages in this conversation yet.");

  const composer = document.querySelector("form[data-field-card]") as HTMLElement;
  const chips = () => within(composer).queryAllByRole("listitem");
  fireEvent.drop(composer, {
    dataTransfer: { types: ["Files"], files: [new File(["body"], "notes.txt")] },
  });
  await waitFor(() => expect(chips().length).toBe(1));

  const send = screen.getByRole("button", { name: "Send" });
  await userEvent.click(send);
  await userEvent.click(send);
  mint();
  await waitFor(() => expect(chips().length).toBe(0));

  const sends = handler.mock.calls.filter(([url]) => String(url).includes("/chat?"));
  expect(sends.length).toBe(1);
  expect((sends[0][1]?.body as FormData).getAll("uploaded_key")).toEqual([
    "artifacts/abc/notes.txt",
  ]);
  expect((sends[0][1]?.body as FormData).getAll("uploaded_sig")).toEqual(["signed"]);
});

test("the card refuses an eleventh file and says so while the member can still see it", async () => {
  wire({
    ...transcript(),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }),
  });
  open();
  await screen.findByText("No messages in this conversation yet.");

  const composer = document.querySelector("form[data-field-card]") as HTMLElement;
  fireEvent.drop(composer, {
    dataTransfer: {
      types: ["Files"],
      files: Array.from({ length: 11 }, (_unused, index) => new File(["body"], index + ".txt")),
    },
  });

  await waitFor(() => expect(within(composer).queryAllByRole("listitem").length).toBe(10));
  expect(within(composer).getByText("A message carries at most 10 files.")).toBeTruthy();
  expect(within(composer).queryByText("10.txt")).toBeNull();
});

test("a file attached while the send runs stays in the card for the next message", async () => {
  let mint: () => void = () => {};
  const minting = new Promise<void>((resolve) => (mint = resolve));
  wire({
    ...transcript(),
    "/uploads": async (_url, init) => {
      await minting;
      const named = JSON.parse(String(init?.body)) as { name: string };
      return json({ key: "artifacts/abc/" + named.name, put_url: "https://store/1", sig: "signed" });
    },
    "store/1": () => new Response("", { status: 200 }),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }),
  });
  open();
  await screen.findByText("No messages in this conversation yet.");

  const composer = document.querySelector("form[data-field-card]") as HTMLElement;
  const chips = () => within(composer).queryAllByRole("listitem");
  const drop = (name: string) =>
    fireEvent.drop(composer, {
      dataTransfer: { types: ["Files"], files: [new File(["body"], name)] },
    });

  drop("sent.txt");
  await waitFor(() => expect(chips().length).toBe(1));
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  drop("later.txt");
  await waitFor(() => expect(chips().length).toBe(2));
  mint();

  await waitFor(() => expect(chips().length).toBe(1));
  expect(within(composer).getByText("later.txt")).toBeTruthy();
});

test("the composer draws a picked image as itself and a picked PDF as a badged card", async () => {
  wire({
    ...transcript(),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }),
  });
  open();
  await screen.findByText("No messages in this conversation yet.");

  const composer = document.querySelector("form[data-field-card]") as HTMLElement;
  fireEvent.drop(composer, {
    dataTransfer: {
      types: ["Files"],
      files: [
        new File(["gif-bytes"], "lights.gif", { type: "image/gif" }),
        new File(["pdf-bytes"], "paper.pdf", { type: "application/pdf" }),
      ],
    },
  });

  const picked = await within(composer).findByRole("img", { name: "lights.gif" });
  expect(picked.getAttribute("src")).toMatch(/^data:image\/gif;base64,/);
  expect(within(composer).getByText("PDF")).toBeTruthy();
  expect(within(composer).getByText("paper.pdf")).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  const sent = await within(screen.getByTestId("log")).findByRole("img", { name: "lights.gif" });
  expect(sent.getAttribute("src")).toMatch(/^data:image\/gif;base64,/);
});

test("the composer asks the preview route for the one page its card draws", async () => {
  const asked: (string | null)[] = [];
  wire({
    ...transcript(),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }),
    "/surface/web/preview": (_url: string, init?: RequestInit) => {
      asked.push((init?.body as FormData).get("pages") as string | null);
      return json({ start_page: 1, page_count: 12, pages: ["cGFnZS0x"] });
    },
  });
  open();
  await screen.findByText("No messages in this conversation yet.");

  const composer = document.querySelector("form[data-field-card]") as HTMLElement;
  fireEvent.drop(composer, {
    dataTransfer: {
      types: ["Files"],
      files: [new File(["pdf-bytes"], "paper.pdf", { type: "application/pdf" })],
    },
  });

  const cover = await within(composer).findByRole("img", { name: "paper.pdf" });
  expect(cover.getAttribute("src")).toBe("data:image/png;base64,cGFnZS0x");
  expect(asked).toEqual(["1"]);
});

test("a reloaded message draws what the member attached rather than naming where it landed", async () => {
  const preview =
    "/surface/web/agents/" + AGENT.id + "/conversations/" + CONVO_ID + "/attachments/web-inbox/lights.gif";
  wire(
    transcript({
      messages: [
        {
          role: "user",
          text: "what are these",
          files: [
            {
              filename: "lights.gif",
              url: null,
              preview_url: preview,
              media_type: "image/gif",
            },
            { filename: "paper.pdf", url: null, preview_url: null, media_type: "application/pdf" },
          ],
        },
      ],
    }),
  );
  open();

  const said = (await screen.findByText("what are these")).closest(
    "[data-slot=message]",
  ) as HTMLElement;
  expect(within(said).getByRole("img", { name: "lights.gif" }).getAttribute("src")).toBe(preview);
  expect(within(said).getByText("PDF")).toBeTruthy();
  expect(within(said).getByText("paper.pdf")).toBeTruthy();
  expect(screen.queryByText(/Attached files/)).toBeNull();
});

test("an attachment whose picture will not load falls back to naming the file", async () => {
  wire(
    transcript({
      messages: [
        {
          role: "user",
          text: "look",
          files: [
            {
              filename: "lights.gif",
              url: null,
              preview_url: "/surface/web/attachments/gone.gif",
              media_type: "image/gif",
            },
          ],
        },
      ],
    }),
  );
  open();

  fireEvent.error(await screen.findByRole("img", { name: "lights.gif" }));
  await waitFor(() => expect(screen.queryByRole("img", { name: "lights.gif" })).toBeNull());
  expect(screen.getByText("lights.gif")).toBeTruthy();
});

test("a member's own attachment opens from the bubble", async () => {
  wire(
    transcript({
      messages: [
        {
          role: "user",
          text: "look",
          files: [
            {
              filename: "lights.gif",
              url: "/dl/lights.gif",
              preview_url: "/artifacts/lights.gif?signed",
              media_type: "image/gif",
            },
          ],
        },
      ],
    }),
  );
  open();

  expect(await screen.findByRole("button", { name: "Open lights.gif" })).toBeTruthy();
});

test("a shared picture whose link will not load falls back to naming the file", async () => {
  wire(
    transcript({
      messages: [
        {
          role: "assistant",
          text: "Here is the portrait.",
          files: [
            {
              filename: "portrait.jpg",
              url: "/dl/portrait.jpg",
              size_bytes: 88_000,
              preview_url: "https://web/artifacts/preview/portrait.jpg?token=signed",
              media_type: "image/jpeg",
            },
          ],
        },
      ],
    }),
  );
  open();

  fireEvent.error(await screen.findByRole("img", { name: "portrait.jpg" }));
  await waitFor(() => expect(screen.queryByRole("img", { name: "portrait.jpg" })).toBeNull());
  expect(screen.getByText("portrait.jpg")).toBeTruthy();
});

test("a file pasted into the message box is attached rather than typed", async () => {
  wire({ ...transcript() });
  open();
  await screen.findByText("No messages in this conversation yet.");

  fireEvent.paste(screen.getByLabelText("Ask UFO"), {
    clipboardData: { files: [new File(["body"], "pasted.txt", { type: "text/plain" })] },
  });

  expect(await screen.findByText("pasted.txt")).toBeTruthy();
  expect((screen.getByLabelText("Ask UFO") as HTMLTextAreaElement).value).toBe("");
});

test("a member who scrolls up while a reply streams stays where they scrolled", async () => {
  wire({ ...transcript(), "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }) });
  open();
  await screen.findByText("No messages in this conversation yet.");
  await userEvent.type(screen.getByLabelText("Ask UFO"), "hi");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));

  const log = screen.getByTestId("log");
  Object.defineProperty(log, "scrollHeight", { configurable: true, value: 1000 });
  Object.defineProperty(log, "clientHeight", { configurable: true, value: 300 });
  log.scrollTop = 0;
  log.dispatchEvent(new Event("scroll"));

  log.appendChild(document.createElement("p"));
  await waitFor(() => expect(log.childElementCount).toBeGreaterThan(0));
  expect(log.scrollTop).toBe(0);
});

test("a sent message anchors the log, and the log animates only for the send window", async () => {
  wire({ ...transcript(), "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }) });
  open();
  await screen.findByText("No messages in this conversation yet.");

  const log = screen.getByTestId("log");
  laidLog(log);
  log.scrollTop = FOOT;
  fireEvent.scroll(log);
  log.scrollTop = 100;
  fireEvent.scroll(log);

  await userEvent.type(screen.getByLabelText("Ask UFO"), "back to now");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  const sent = (await screen.findByText("back to now")).closest(
    "[data-slot=message-scroller-item]",
  )!;
  expect(sent.getAttribute("data-scroll-anchor")).toBe("true");
  expect(log.classList).toContain("scroll-smooth");

  await waitFor(() => expect(log.classList).not.toContain("scroll-smooth"), { timeout: 3_000 });
});

test("answering a question while scrolled up re-pins the log to the bottom", async () => {
  wire({
    ...transcript({ messages: [ASKING(ASKED)] }),
    "/chat": () => json({ turn_id: REFUSED_TURN, body: "left" }),
  });
  open();
  await screen.findByText("asking");

  const log = screen.getByTestId("log");
  laidLog(log);
  log.scrollTop = FOOT;
  fireEvent.scroll(log);
  log.scrollTop = 100;
  fireEvent.scroll(log);

  await userEvent.click(screen.getByRole("radio", { name: "left" }));
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  await waitFor(() => expect(log.scrollTop).toBe(FOOT));
});

test("switching conversations remounts the log so scroll state never leaks across", async () => {
  const other = {
    ...CHAT_ROW,
    conversation_id: "66666666-6666-4666-8666-666666666666",
    agent_id: SECOND_ID,
    agent_name: "second",
    title: "The second thread",
  };
  wire({
    ...chatsOnWire([CHAT_ROW, other]),
    "/transcript": () => json({ messages: [] }),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }),
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);
  await screen.findByText("No messages in this conversation yet.");

  const first = screen.getByTestId("log");
  laidLog(first);
  first.scrollTop = FOOT;
  fireEvent.scroll(first);
  first.scrollTop = 100;
  fireEvent.scroll(first);

  follow(chatHash(other.conversation_id));
  await screen.findByText("No messages in this conversation yet.");
  const fresh = screen.getByTestId("log");
  expect(fresh).not.toBe(first);

  laidLog(fresh);
  await userEvent.type(screen.getByLabelText("Ask UFO"), "hello there");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByText("hello there");
  expect(first.isConnected).toBe(false);
  expect(fresh.scrollTop).toBe(0);
});

test("the log opens pinned: loading a transcript lands at the bottom untouched", async () => {
  let release: (value: Response) => void = () => {};
  wire({
    ...chatsOnWire([CHAT_ROW]),
    "/transcript": () => new Promise<Response>((resolve) => (release = resolve)),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }),
  });
  open();

  const log = await screen.findByTestId("log");
  laidLog(log);

  release(json({ messages: [{ role: "assistant", text: "history" }] }));
  await screen.findByText("history");
  expect(log.scrollTop).toBe(FOOT);
});

test("a reader inside the tolerance band still counts as at the bottom", async () => {
  wire({ ...transcript(), "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }) });
  open();
  await screen.findByText("No messages in this conversation yet.");
  await userEvent.type(screen.getByLabelText("Ask UFO"), "hi");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));

  const log = screen.getByTestId("log");
  laidLog(log);
  log.scrollTop = FOOT;
  fireEvent.scroll(log);
  log.scrollTop = FOOT - 10;
  fireEvent.scroll(log);
  expect(screen.getByRole("button", { name: "Jump to bottom" }).getAttribute("data-active")).toBe(
    "false",
  );

  StreamFake.last().emit("message", { text: "nudged" });
  await screen.findByText(saying("nudged"));
  lands(log);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Jump to bottom" }).getAttribute("data-active")).toBe(
      "false",
    ),
  );
  expect(log.scrollTop).toBe(FOOT - 10);
});

test("sending returns focus to the composer instead of stranding it on the page", async () => {
  wire({ ...transcript(), "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }) });
  open();
  await screen.findByText("No messages in this conversation yet.");
  const input = screen.getByLabelText("Ask UFO");
  await userEvent.type(input, "hi");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  expect(document.activeElement).toBe(input);
});

test("answering a question returns focus to the composer", async () => {
  wire({
    ...transcript({ messages: [ASKING(ASKED)] }),
    "/chat": () => json({ turn_id: REFUSED_TURN, body: "left" }),
  });
  open();
  await userEvent.click(await screen.findByRole("radio", { name: "left" }));
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  await waitFor(() =>
    expect(document.activeElement).toBe(screen.getByLabelText("Ask UFO")),
  );
});

test("a second answer clicked mid-stream neither posts nor yanks the reader", async () => {
  const posts: string[] = [];
  wire({
    ...transcript({
      messages: [
        {
          role: "assistant",
          text: "asking",
          question: {
            turn_id: TURN_ID,
            title: "Two things",
            questions: [
              { question: "First?", options: [{ label: "alpha" }, { label: "beta" }] },
              { question: "Second?", options: [{ label: "gamma" }, { label: "delta" }] },
            ],
          },
        },
      ],
    }),
    "/chat": (url) => {
      posts.push(url);
      return json({ turn_id: REFUSED_TURN, body: "alpha · First?" });
    },
  });
  open();
  await userEvent.click(await screen.findByRole("radio", { name: "alpha" }));
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  await userEvent.click(screen.getByRole("button", { name: "Skip" }));
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  await waitFor(() => expect(posts.length).toBe(1));

  const log = screen.getByTestId("log");
  Object.defineProperty(log, "scrollHeight", { configurable: true, value: 1000 });
  Object.defineProperty(log, "clientHeight", { configurable: true, value: 300 });
  log.scrollTop = 100;
  fireEvent.scroll(log);

  await userEvent.click(await screen.findByRole("radio", { name: "gamma" }));
  const submit = screen.getByRole("button", { name: "Continue" });
  expect(submit.hasAttribute("disabled")).toBe(true);
  await userEvent.click(submit);
  expect(posts.length).toBe(1);
  StreamFake.last().emit("message", { text: "still streaming" });
  await screen.findByText(saying(/still streaming/));
  expect(log.scrollTop).toBe(100);
});

test("agent markdown renders as elements while the member's text stays literal", async () => {
  wire({ ...transcript(), "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }) });
  open();
  await screen.findByText("No messages in this conversation yet.");
  await userEvent.type(screen.getByLabelText("Ask UFO"), "**hi**");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));

  expect(screen.getByText("**hi**")).toBeTruthy();
  StreamFake.last().emit("message", { text: "see [docs][ref]\n\n" });
  StreamFake.last().emit("message", { text: "**mid**" });
  expect((await screen.findByText(saying("mid"))).closest("strong")).not.toBeNull();
  StreamFake.last().emit("message", { text: "\n\n**bold**\n\n[ref]: https://example.com/d\n" });
  StreamFake.last().emit("terminal", {
    status: "done",
    model: "opus",
    tokens: 1,
    cost_micro_usd: 0,
  });

  expect((await screen.findByText("bold")).tagName).toBe("STRONG");
  const agentSide = screen.getByText("bold").closest("[data-slot=bubble-content]")!;
  expect(agentSide.className).not.toContain("whitespace-pre-wrap");
  const mineSide = screen.getByText("**hi**").closest("[data-slot=bubble-content]")!;
  expect(mineSide.className).toContain("whitespace-pre-wrap");
  const link = await screen.findByRole("link", { name: "docs" });
  expect(link.getAttribute("href")).toBe("https://example.com/d");
  expect(screen.getByText("**hi**").textContent).toBe("**hi**");
});

test("an address a member sent is a link in the member's own bubble", async () => {
  wire({ ...transcript(), "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }) });
  open();
  await screen.findByText("No messages in this conversation yet.");
  await userEvent.type(screen.getByLabelText("Ask UFO"), "read https://example.com/d");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  const link = await screen.findByRole("link", { name: "https://example.com/d" });
  expect(link.getAttribute("href")).toBe("https://example.com/d");
  expect(link.getAttribute("target")).toBe("_blank");
  expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  expect(link.closest("[data-role=me]")).not.toBeNull();
});

test("a turn the fleet picked back up says so on its working line", async () => {
  wire(transcript({ messages: [{ role: "user", text: "Run the migration." }], turn: TURN_ID }));
  open();

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("activity", { text: "Applying the migration." });
  StreamFake.last().emit("resumed", { attempt: "attempt-one" });

  const resumed = await screen.findByText("Resumed after a restart");
  expect(resumed.closest("[data-slot=marker]")).toBeTruthy();
  expect(screen.queryByText("Applying the migration.")).toBeNull();
});

test("an opened thread's box asks for a follow-up, and the start screen's starts a new chat", async () => {
  wire(transcript({ messages: [{ role: "user", text: "Review PR 1268." }] }));
  open();

  const box = (await screen.findByLabelText("Ask UFO")) as HTMLTextAreaElement;
  expect(box.placeholder).toBe("Ask a follow-up…");

  cleanup();
  location.hash = newChatHash(AGENT_ID);
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const starting = (await screen.findByLabelText("Ask UFO")) as HTMLTextAreaElement;
  expect(starting.placeholder).toBe("Start new chat…");
});

test("a new conversation's composer offers no way to switch app", async () => {
  wire({
    ...transcript(),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }),
  });
  location.hash = newChatHash(AGENT_ID);
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByLabelText("Ask UFO");
  expect(screen.queryByTestId("log")).toBeNull();
  expect(screen.queryByRole("navigation", { name: "Breadcrumb" })).toBeNull();
  expect(screen.queryByRole("combobox", { name: "App" })).toBeNull();
  expect(screen.getByRole("button", { name: "Attach files" })).toBeTruthy();

  await userEvent.type(screen.getByLabelText("Ask UFO"), "hello");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  expect(screen.queryByRole("combobox", { name: "App" })).toBeNull();
});

test("a new conversation opens with the composer focused", async () => {
  wire({ ...transcript() });
  location.hash = newChatHash(AGENT_ID);
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const box = await screen.findByLabelText("Ask UFO");
  expect(document.activeElement).toBe(box);
});

test("a thread opened from the rail lands the cursor in the composer", async () => {
  wire({ ...transcript() });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const box = await screen.findByLabelText("Ask UFO");
  await waitFor(() => expect(document.activeElement).toBe(box));
});

test("a thread opened at a phone width leaves the composer alone, so no keyboard rises", async () => {
  atPhoneWidth();
  wire({ ...transcript() });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const box = await screen.findByLabelText("Ask UFO");
  expect(document.activeElement).not.toBe(box);
});

test("the composer's attach act stands on the centre line of the send act", async () => {
  wire({ ...transcript() });
  location.hash = newChatHash(AGENT_ID);
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByLabelText("Ask UFO");
  const attach = screen.getByRole("button", { name: "Attach files" });
  const toolbar = attach.parentElement!;

  expect(toolbar.contains(screen.getByRole("button", { name: "Send" }))).toBe(true);
  expect(toolbar.className).toContain("items-center");
});

test("a route that renames the start screen's agent reads that agent's own draft", async () => {
  wire({ ...transcript() });
  localStorage.setItem("ufo.chat-draft." + MEMBER.id + "/new:" + AGENT_ID, "words for the main agent");
  location.hash = "#/new/" + SECOND_ID;
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  const box = (await screen.findByLabelText("Ask UFO")) as HTMLTextAreaElement;
  await userEvent.type(box, "words for the second");

  follow(newChatHash(AGENT_ID));

  await waitFor(() => expect(box.value).toBe("words for the main agent"));
  expect(localStorage.getItem("ufo.chat-draft." + MEMBER.id + "/new:" + SECOND_ID)).toBe(
    "words for the second",
  );
});

test("the start screen heads the box with the ufo wordmark, drawn above the phone width", async () => {
  wire({ ...transcript() });
  location.hash = newChatHash(AGENT_ID);
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const start = await screen.findByTestId("start");
  const wordmark = within(start).getByRole("img", { name: "ufo" });
  expect(wordmark.getAttribute("style")).toContain("ufo-logo.svg");
  expect(wordmark.className).toContain("h-(--size-wordmark-hero)");
  expect(wordmark.className).toContain("mb-8xl");
  expect(wordmark.className).toContain("max-narrow:hidden");
  expect(
    wordmark.compareDocumentPosition(screen.getByLabelText("Ask UFO")) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
});

test("the start screen's empty space is the box's: a press in it lands the cursor in the words", async () => {
  wire({ ...transcript() });
  location.hash = newChatHash(AGENT_ID);
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const box = (await screen.findByLabelText("Ask UFO")) as HTMLTextAreaElement;
  await userEvent.type(box, "draft this");
  box.setSelectionRange(4, 4);
  box.blur();
  expect(document.activeElement).not.toBe(box);

  fireEvent.mouseDown(screen.getByTestId("start"));

  expect(document.activeElement).toBe(box);
  expect(box.selectionStart).toBe(4);

  await userEvent.click(screen.getByRole("button", { name: "Attach files" }));
  expect(document.activeElement).not.toBe(box);
});

test("a starter says its sentence on the press, and leaves with the start screen", async () => {
  const said: string[] = [];
  wire({
    ...transcript(),
    "/chat": (_url, init) => {
      said.push(String(init?.body));
      return json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" });
    },
  });
  location.hash = newChatHash(AGENT_ID);
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByLabelText("Ask UFO");
  await userEvent.click(screen.getByRole("button", { name: /competitors you name/ }));

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  expect(said).toEqual([
    "I want an application that tracks the competitors I name and writes up what changed, with a source for each claim.",
  ]);
  expect((screen.getByLabelText("Ask UFO") as HTMLTextAreaElement).value).toBe("");
  expect(location.hash).toBe(chatHash(CONVO_ID));
  expect(screen.queryByRole("button", { name: /competitors you name/ })).toBeNull();
});

test("a pressed starter counts the press, by the kind of row it was", async () => {
  const posts: RequestInit[] = [];
  wire({
    ...transcript(),
    "/workspace/starters": () => json(SLATE),
    "/chat": (_url, init) => {
      posts.push(init ?? {});
      return json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" });
    },
  });
  location.hash = newChatHash(AGENT_ID);
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: /renewal was waiting on legal/ }));

  await waitFor(() => expect(posts.length).toBe(1));
  const headers = posts[0].headers as Record<string, string>;
  expect(headers["x-ufo-click"]).toBe("starter");
  expect(headers["x-ufo-click-kind"]).toBe("check_in");
});

test("the composer names the app it addresses, and the band can be taken away", async () => {
  wire(transcript());
  location.hash = "#/new/" + SECOND_ID;
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByLabelText("Ask UFO");

  const page = within(screen.getByRole("main"));
  const band = page.getByText("Second");
  expect(band.parentElement!.querySelector("svg")).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Stop addressing Second" }));

  expect(page.queryByText("Second")).toBeNull();
  expect(screen.getByLabelText("Ask UFO")).toBeTruthy();
});

test("the chat app's composer does not name itself over its own words", async () => {
  wire(transcript());
  location.hash = "#/agents/" + CHAT_APP_ID + "?open=compose";
  render(<App agents={[AGENT, CHAT_APP]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByLabelText("Ask UFO");
  expect(screen.queryByRole("button", { name: /^Stop addressing/ })).toBeNull();
});

const SLATE = {
  starters: [
    {
      kind: "app",
      mark: "omphalos",
      line: "Report cash, burn, and the months of runway left.",
      ask: "I want an application that reports our cash, burn, and months of runway.",
    },
    {
      kind: "app",
      mark: "hydria",
      line: "Draft the chase for every invoice past its terms.",
      ask: "I want an application that drafts the chase for every overdue invoice.",
    },
    {
      kind: "unlock",
      mark: "kalathos",
      line: "Name the accounts each deal is still waiting on.",
      ask: "I want an application that names the accounts each deal waits on.",
      providers: [{ name: "salesforce", label: "Salesforce" }],
    },
    {
      kind: "check_in",
      mark: null,
      line: "The renewal was waiting on legal last week.",
      ask: "Where did the Acme renewal land?",
    },
  ],
  unlock: {
    line: "Keep a one-page brief per account current.",
    ask: "I want an application that keeps a one-page brief per account current.",
    providers: [
      { name: "hubspot", label: "HubSpot" },
      { name: "notion", label: "Notion" },
    ],
  },
};

test("a workspace with nothing ranked reads the rows the screen ships with", async () => {
  wire({
    ...transcript(),
    "/workspace/starters": () => json({ starters: [], unlock: null }),
  });
  location.hash = newChatHash(AGENT_ID);
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByRole("button", { name: /competitors you name/ });
  expect(
    screen.getByRole("button", { name: /market, company, or person/ }),
  ).toBeTruthy();
  expect(
    screen.getByRole("link", { name: /Connect more accounts/ }),
  ).toBeTruthy();
});

test("no connect act is drawn while the ranking is read", async () => {
  let rank: (() => void) | null = null;
  const ranked = new Promise<void>((settle) => {
    rank = settle;
  });
  wire({
    ...transcript(),
    "/workspace/starters": async () => {
      await ranked;
      return json(SLATE);
    },
  });
  location.hash = newChatHash(AGENT_ID);
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const rows = (await screen.findByRole("button", { name: /competitors you name/ }))
    .parentElement!;
  expect(screen.queryByRole("link", { name: /Connect more accounts/ })).toBeNull();
  expect(rows.querySelectorAll("[data-part=skeleton]")).toHaveLength(2);

  rank!();

  expect(
    await screen.findByRole("button", { name: /one-page brief per account/ }),
  ).toBeTruthy();
  expect(screen.queryByRole("link", { name: /Connect more accounts/ })).toBeNull();
  expect(rows.querySelectorAll("[data-part=skeleton]")).toHaveLength(0);
});

test("a ranked slate replaces every row the screen ships with", async () => {
  wire({ ...transcript(), "/workspace/starters": () => json(SLATE) });
  location.hash = newChatHash(AGENT_ID);
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByRole("button", { name: /months of runway left/ });
  expect(screen.getByRole("button", { name: /invoice past its terms/ })).toBeTruthy();
  expect(screen.getByRole("button", { name: /waiting on legal/ })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /competitors you name/ })).toBeNull();
});

test("a spare unlock stands in an app's slot and wears the brand of what it needs", async () => {
  wire({ ...transcript(), "/workspace/starters": () => json(SLATE) });
  location.hash = newChatHash(AGENT_ID);
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const row = await screen.findByRole("button", { name: /accounts each deal is still waiting on/ });
  expect(row.textContent).not.toContain("Connect");

  const glyph = row.firstElementChild!;
  expect(glyph.getAttribute("style")).toContain("--brand-salesforce");
  const mark = glyph.getAttribute("class") ?? "";
  expect(mark).toContain("size-(--size-glyph)");
  expect(mark).not.toContain("--size-brand-mark");
});

test("a ranked starter says its own sentence, and a check-in asks after work", async () => {
  const said: string[] = [];
  wire({
    ...transcript(),
    "/workspace/starters": () => json(SLATE),
    "/chat": (_url, init) => {
      said.push(String(init?.body));
      return json({
        turn_id: TURN_ID,
        conversation_id: CONVO_ID,
        title: "hello",
      });
    },
  });
  location.hash = newChatHash(AGENT_ID);
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(
    await screen.findByRole("button", { name: /waiting on legal/ }),
  );

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  expect(said).toEqual(["Where did the Acme renewal land?"]);
});

test("a default-app starter opens that app and says the ranked ask there", async () => {
  const ask = "Set up this app.";
  const said: string[] = [];
  const posts: string[] = [];
  wire({
    ...transcript(),
    "/workspace/starters": () =>
      json({
        starters: [
          {
            kind: "app",
            mark: "gnomon",
            line: "Report what each pull request waits on.",
            ask,
            agent_id: SECOND_ID,
          },
          {
            kind: "app",
            mark: "pinax",
            line: "Assign each issue to its owner.",
            ask,
            agent_id: AGENT_ID,
          },
        ],
        unlock: null,
      }),
    "/chat": (url, init) => {
      posts.push(url);
      said.push(String(init?.body));
      return json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" });
    },
  });
  location.hash = newChatHash(AGENT_ID);
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByRole("button", { name: /each issue to its owner/ })).toBeTruthy();
  await userEvent.click(
    await screen.findByRole("button", { name: /each pull request waits on/ }),
  );

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  expect(posts[0]).toContain(SECOND_ID);
  expect(said).toEqual([ask]);
});

test("an unlock is an ask, not a departure: it names its accounts and says the build", async () => {
  const said: string[] = [];
  wire({
    ...transcript(),
    "/workspace/starters": () => json(SLATE),
    "/chat": (_url, init) => {
      said.push(String(init?.body));
      return json({
        turn_id: TURN_ID,
        conversation_id: CONVO_ID,
        title: "hello",
      });
    },
  });
  location.hash = newChatHash(AGENT_ID);
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const row = await screen.findByRole("button", { name: /one-page brief per account/ });
  expect(row.textContent).toContain("Connect HubSpot and Notion.");
  expect(
    screen.queryByRole("link", { name: /Connect more accounts/ }),
  ).toBeNull();

  const mark = row.firstElementChild!.getAttribute("class") ?? "";
  expect(mark).toContain("size-(--size-glyph)");
  expect(mark).not.toContain("--size-brand-mark");

  await userEvent.click(row);
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  expect(said).toEqual([
    "I want an application that keeps a one-page brief per account current.",
  ]);
});

test("a landed connect states the account it made, and presses nothing", async () => {
  wire(
    transcript({
      messages: [
        { role: "user", text: "connect github" },
        {
          role: "assistant",
          text: "Use the connection control.",
          connect: { provider: "github", label: "GitHub", account: "Work account" },
        },
      ],
    }),
  );
  open();

  const settled = await screen.findByText("GitHub connected");

  expect(settled.closest("a")).toBeNull();
  expect(screen.getByText("Work account")).toBeTruthy();
  expect(screen.queryByRole("link", { name: /Connect/ })).toBeNull();
  expect(screen.getByText("Use the connection control.")).toBeTruthy();
});

test("a standing connect is the act, pressable to the provider's own page", async () => {
  wire(
    transcript({
      messages: [
        { role: "user", text: "connect github" },
        {
          role: "assistant",
          text: "Use the connection control.",
          connect: { provider: "github", label: "GitHub", turn: TURN_ID },
        },
      ],
    }),
  );
  open();

  const act = await screen.findByRole("link", { name: "Connect GitHub" });

  expect(act.getAttribute("href")).toBe("/surface/web/turns/" + TURN_ID + "/connect");
});

test("the starters close on a link to the connectors screen, which the press reaches", async () => {
  wire({
    ...transcript(),
    "/connections": () => json({ connections: [] }),
    "/workspace/first-run": () => json({ providers: [], mcp_servers: [], connectors: [] }),
  });
  location.hash = newChatHash(AGENT_ID);
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByLabelText("Ask UFO");
  const cta = screen.getByRole("link", { name: /Connect more accounts/ });
  expect(cta.getAttribute("href")).toBe("#/connectors");

  await userEvent.click(cta);

  expect(location.hash).toBe("#/connectors");
  await screen.findByRole("link", { name: "Add credential" });
  expect(screen.queryByRole("button", { name: /open pull request/ })).toBeNull();
});

/** The model the chat POST pinned on the turn it founded, or null where the send carried
 *  no pick. */
function pinned(init: RequestInit | undefined): string | null {
  return new Headers(init?.headers).get("x-ufo-model");
}

/** The chat POST is the only route a pick reaches: `/intents` has no route here, so an agent apply
 *  intent fails the test where it is posted. */
function picking(sent: (string | null)[]): Record<string, Route> {
  return {
    ...transcript(),
    "/chat": (_url, init) => {
      sent.push(pinned(init));
      return json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "go" });
    },
  };
}

async function say(text: string): Promise<void> {
  await userEvent.type(screen.getByLabelText("Ask UFO"), text);
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
}

test("the model chip picks out of a provider flyout, and the pick rides the message", async () => {
  const sent: (string | null)[] = [];
  wire(picking(sent));
  render(
    <App agents={[{ ...AGENT, model: "claude-opus-4-8" }]} member={MEMBER} onAgents={() => {}} />,
  );

  await userEvent.click(await screen.findByRole("button", { name: "Model: Opus 4.8" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "Claude" }));
  expect(
    (await screen.findByRole("menuitemradio", { name: "Opus 5" })).getAttribute("aria-checked"),
  ).toBe("false");

  await userEvent.click(screen.getByRole("menuitem", { name: "GPT" }));
  const pick = await screen.findByRole("menuitemradio", { name: "GPT-5.6 Sol" });
  expect(pick.getAttribute("aria-checked")).toBe("false");
  await userEvent.click(pick);

  expect(await screen.findByRole("button", { name: "Model: GPT-5.6 Sol" })).toBeTruthy();
  await say("hello");

  expect(sent).toEqual(["gpt-5.6-sol"]);
});

test("a member who may not write the agent row picks a model for the thread", async () => {
  const sent: (string | null)[] = [];
  wire(picking(sent));
  render(
    <App
      agents={[{ ...AGENT, model: "claude-opus-4-8", mine: false }]}
      member={MEMBER}
      onAgents={() => {}}
    />,
  );

  await userEvent.click(await screen.findByRole("button", { name: "Model: Opus 4.8" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "GPT" }));
  await userEvent.click(await screen.findByRole("menuitemradio", { name: "GPT-5.6 Sol" }));
  await say("hello");

  expect(sent).toEqual(["gpt-5.6-sol"]);
});

test("every message after a pick carries it, one before it the agent's own model", async () => {
  const sent: (string | null)[] = [];
  wire(picking(sent));
  render(
    <App agents={[{ ...AGENT, model: "claude-opus-4-8" }]} member={MEMBER} onAgents={() => {}} />,
  );

  await screen.findByLabelText("Ask UFO");
  await say("first");
  await userEvent.click(await screen.findByRole("button", { name: "Model: Opus 4.8" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "GPT" }));
  await userEvent.click(await screen.findByRole("menuitemradio", { name: "GPT-5.6 Sol" }));
  await say("second");
  await say("third");

  expect(sent).toEqual([null, "gpt-5.6-sol", "gpt-5.6-sol"]);
});

test("a pick lands on the new chat screen and rides the message it founds", async () => {
  const sent: (string | null)[] = [];
  wire({
    ...transcript(),
    "/chat": (_url, init) => {
      sent.push(new Headers(init?.headers).get("x-ufo-model"));
      return json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "go" });
    },
  });
  location.hash = newChatHash(AGENT_ID);
  render(
    <App agents={[{ ...AGENT, model: "claude-opus-4-8" }]} member={MEMBER} onAgents={() => {}} />,
  );

  await userEvent.click(await screen.findByRole("button", { name: "Model: Opus 4.8" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "GPT" }));
  await userEvent.click(await screen.findByRole("menuitemradio", { name: "GPT-5.6 Sol" }));

  expect(await screen.findByRole("button", { name: "Model: GPT-5.6 Sol" })).toBeTruthy();
  expect(screen.queryAllByRole("menu")).toEqual([]);

  await userEvent.type(screen.getByLabelText("Ask UFO"), "hello");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  expect(sent).toEqual(["gpt-5.6-sol"]);
});

/** The rows of the flyout a model stands in: a submenu that has just closed lingers in the tree
 *  until Radix unmounts it, so one flyout is read through a row of its own. */
async function flyoutRows(model: string) {
  const row = await screen.findByRole("menuitemradio", { name: model });
  const flyout = row.closest("[role='menu']") as HTMLElement;
  return within(flyout)
    .getAllByRole("menuitemradio")
    .map((item) => item.textContent);
}

test("the model picker offers the latest of each family, a speed variant on its own row", async () => {
  wire(transcript());
  render(
    <App agents={[{ ...AGENT, model: "claude-opus-4-8" }]} member={MEMBER} onAgents={() => {}} />,
  );

  await userEvent.click(await screen.findByRole("button", { name: "Model: Opus 4.8" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "Claude" }));
  await screen.findByRole("menuitemradio", { name: "Opus 5" });
  expect(screen.queryByRole("menuitemradio", { name: "Opus 4.8" })).toBeNull();
  expect(screen.queryByRole("menuitemradio", { name: "Sonnet 4.6" })).toBeNull();
  expect(screen.queryByRole("menuitemradio", { name: "Haiku 4.5" })).toBeNull();

  await userEvent.click(screen.getByRole("menuitem", { name: "GPT" }));
  expect(await flyoutRows("GPT-6 Astra")).toEqual([
    "GPT-6 Astra",
    "GPT-5.6 Sol",
    "GPT-5.6 Terra",
    "GPT-5.6 Luna",
  ]);
});

test("the picker offers Claude largest first and stands Claude and GPT alone", async () => {
  wire(transcript());
  render(
    <App agents={[{ ...AGENT, model: "claude-opus-4-8" }]} member={MEMBER} onAgents={() => {}} />,
  );

  await userEvent.click(await screen.findByRole("button", { name: "Model: Opus 4.8" }));
  const providers = await screen.findByRole("menu");
  expect(
    within(providers)
      .getAllByRole("menuitem")
      .map((item) => item.textContent),
  ).toEqual(["Claude", "GPT"]);

  await userEvent.click(screen.getByRole("menuitem", { name: "Claude" }));
  expect(await flyoutRows("Opus 5")).toEqual(["Fable 5.1", "Opus 5", "Sonnet 5"]);
});

test("an agent on auto reads Auto on the chip and the picker ticks the Auto row", async () => {
  wire(transcript());
  render(<App agents={[{ ...AGENT, model: "auto" }]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Model: Auto" }));
  const auto = await screen.findByRole("menuitemradio", { name: "Auto" });
  expect(auto.getAttribute("aria-checked")).toBe("true");

  await userEvent.click(await screen.findByRole("menuitem", { name: "Claude" }));
  expect(
    (await screen.findByRole("menuitemradio", { name: "Opus 5" })).getAttribute("aria-checked"),
  ).toBe("false");
});

test("the Auto row drops the pick, and the agent's own model runs the next message", async () => {
  const sent: (string | null)[] = [];
  wire(picking(sent));
  render(
    <App agents={[{ ...AGENT, model: "claude-opus-4-8" }]} member={MEMBER} onAgents={() => {}} />,
  );

  await userEvent.click(await screen.findByRole("button", { name: "Model: Opus 4.8" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "GPT" }));
  await userEvent.click(await screen.findByRole("menuitemradio", { name: "GPT-5.6 Sol" }));
  await say("first");

  await userEvent.click(await screen.findByRole("button", { name: "Model: GPT-5.6 Sol" }));
  await userEvent.click(await screen.findByRole("menuitemradio", { name: "Auto" }));

  expect(await screen.findByRole("button", { name: "Model: Opus 4.8" })).toBeTruthy();
  await say("second");

  expect(sent).toEqual(["gpt-5.6-sol", null]);
});

test("an agent on auto pins the pick, and the Auto row hands the choice back", async () => {
  const sent: (string | null)[] = [];
  wire(picking(sent));
  render(<App agents={[{ ...AGENT, model: "auto" }]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Model: Auto" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "GPT" }));
  await userEvent.click(await screen.findByRole("menuitemradio", { name: "GPT-5.6 Sol" }));
  await say("first");

  await userEvent.click(await screen.findByRole("button", { name: "Model: GPT-5.6 Sol" }));
  await userEvent.click(await screen.findByRole("menuitemradio", { name: "Auto" }));
  await say("second");

  expect(sent).toEqual(["gpt-5.6-sol", null]);
  expect(await screen.findByRole("button", { name: "Model: Auto" })).toBeTruthy();
});

test("the box handed to the next agent drops the model picked for the last one", async () => {
  wire(transcript());
  location.hash = newChatHash(AGENT_ID);
  render(
    <App
      agents={[
        { ...AGENT, model: "claude-opus-4-8" },
        { ...SECOND, model: "claude-opus-4-8" },
      ]}
      member={MEMBER}
      onAgents={() => {}}
    />,
  );

  await userEvent.click(await screen.findByRole("button", { name: "Model: Opus 4.8" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "GPT" }));
  await userEvent.click(await screen.findByRole("menuitemradio", { name: "GPT-5.6 Sol" }));
  expect(await screen.findByRole("button", { name: "Model: GPT-5.6 Sol" })).toBeTruthy();

  follow(newChatHash(SECOND_ID));

  expect(await screen.findByRole("button", { name: "Model: Opus 4.8" })).toBeTruthy();
});

test("the thread's pick stands while the agent's own model moves under it", async () => {
  wire(transcript());
  const { rerender } = render(
    <App agents={[{ ...AGENT, model: "claude-opus-4-8" }]} member={MEMBER} onAgents={() => {}} />,
  );

  await userEvent.click(await screen.findByRole("button", { name: "Model: Opus 4.8" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "GPT" }));
  await userEvent.click(await screen.findByRole("menuitemradio", { name: "GPT-5.6 Sol" }));
  expect(await screen.findByRole("button", { name: "Model: GPT-5.6 Sol" })).toBeTruthy();

  rerender(<App agents={[{ ...AGENT, model: "auto" }]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByRole("button", { name: "Model: GPT-5.6 Sol" })).toBeTruthy();
});

test("the model picker's rows read left to right while the flyout stands to the left", async () => {
  wire(transcript());
  render(
    <App agents={[{ ...AGENT, model: "claude-opus-4-8" }]} member={MEMBER} onAgents={() => {}} />,
  );

  await userEvent.click(await screen.findByRole("button", { name: "Model: Opus 4.8" }));
  const providers = await screen.findByRole("menu");
  expect(providers.getAttribute("dir")).toBe("ltr");

  await userEvent.click(await screen.findByRole("menuitem", { name: "Claude" }));
  const models = screen
    .getAllByRole("menu")
    .find((menu) => menu !== providers) as HTMLElement;
  expect(models.getAttribute("dir")).toBe("ltr");
  expect(models.getAttribute("data-side")).toBe("left");
});

test("at a phone width the picker keeps its flyout, and a tap on a model shuts both menus", async () => {
  const sent: (string | null)[] = [];
  atPhoneWidth();
  wire(picking(sent));
  render(
    <App agents={[{ ...AGENT, model: "claude-opus-4-8" }]} member={MEMBER} onAgents={() => {}} />,
  );
  const touch = userEvent.setup();

  await touch.pointer([
    { keys: "[TouchA]", target: await screen.findByRole("button", { name: "Model: Opus 4.8" }) },
  ]);
  const providers = await screen.findByRole("menu");

  await touch.pointer([
    { keys: "[TouchA]", target: await screen.findByRole("menuitem", { name: "GPT" }) },
  ]);
  const luna = await screen.findByRole("menuitemradio", { name: "GPT-5.6 Luna" });
  expect(luna.closest("[role='menu']")).not.toBe(providers);

  await touch.pointer([{ keys: "[TouchA]", target: luna }]);

  expect(await screen.findByRole("button", { name: "Model: GPT-5.6 Luna" })).toBeTruthy();
  await waitFor(() => expect(screen.queryAllByRole("menu")).toEqual([]));
  await say("hello");

  expect(sent).toEqual(["gpt-5.6-luna"]);
});

test("the shell chip is drawn for a conversation that did coding work, and for no other", async () => {
  wire({
    ...transcript(),
    "/shell$": () => json({ available: false, active: false }),
  });
  open();

  expect(await screen.findByRole("button", { name: "Changes" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Shell" })).toBeNull();
  cleanup();

  wire({
    ...transcript(),
    "/shell$": () => json({ available: true, active: true }),
  });
  open();

  const chip = await screen.findByRole("button", { name: "Shell, sandbox running" });
  expect(chip.getAttribute("aria-pressed")).toBe("false");
});

const SLACK_THREAD = {
  id: CONVO_ID,
  agent: { id: AGENT_ID, name: "assistant" },
  surface: "slack",
  surface_label: "#general",
  audience: "room:slack:C1",
  member_email: null,
  description: "",
  source: "https://example.slack.com/archives/C1/p1789239497408479",
  speakers: [],
  turn_count: 2,
  created_at: "2026-08-01T09:00:00Z",
  last_turn_at: "2026-08-01T09:05:00Z",
  readable: true,
  disclosable: false,
  speakable: false,
};

test("a thread that arrived on Slack draws the slot chips and the shell beside its channel link", async () => {
  wire({
    "/api/chats": () => json({ conversation: SLACK_THREAD }),
    "/transcript": () => json({ messages: [] }),
    "/slots": () =>
      json({
        slots: [
          { id: "artifacts", label: "Artifacts", icon: "artifact", kind: "artifacts", count: 2 },
        ],
      }),
    "/shell$": () => json({ available: true, active: true }),
  });
  open();

  const link = await screen.findByRole("link", { name: "Open #general in Slack" });
  expect(link.getAttribute("href")).toBe(SLACK_THREAD.source);
  expect(await screen.findByRole("button", { name: "Artifacts 2" })).toBeTruthy();
  expect(await screen.findByRole("button", { name: "Shell, sandbox running" })).toBeTruthy();
});

test("a read-only chat draws no composer, and its one control stops the live turn", async () => {
  const { handler } = wire({
    ...transcript({ messages: [], turn: TURN_ID, turn_started_at: "2026-08-14T09:00:00Z" }),
    "/chat": () => json({ stopped: true }),
  });
  render(<Chat agent={AGENT} member={MEMBER} conversationId={CONVO_ID} readOnly stops={TURN_ID} />);

  const stop = await screen.findByRole("button", { name: "Stop" });
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.queryByRole("button", { name: "Send" })).toBeNull();

  await userEvent.click(stop);
  const stops = () =>
    handler.mock.calls.filter(([, init]) => (init?.headers as Record<string, string>)?.["x-ufo-stop-turn"]);
  await waitFor(() => expect(stops()).toHaveLength(1));
  StreamFake.last().emit("terminal", { status: "cancelled" });
  await waitFor(() => expect(screen.queryByRole("button", { name: "Stop" })).toBeNull());
});

test("a read-only chat stops only the run it names, never the conversation's other live turn", async () => {
  wire(transcript({ messages: [], turn: TURN_ID, turn_started_at: "2026-08-14T09:00:00Z" }));
  render(<Chat agent={AGENT} member={MEMBER} conversationId={CONVO_ID} readOnly stops={ARRIVAL_ID} />);

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  expect(screen.queryByRole("textbox")).toBeNull();
});

/** The shell socket, which jsdom has none of. A terminal opened here states its grid, takes the
 *  bytes the member types, and ends when the test says the far end closed. */
class SocketFake {
  static opened: SocketFake[] = [];
  static OPEN = 1;
  readyState = SocketFake.OPEN;
  binaryType = "";
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  sent: unknown[] = [];

  constructor(readonly url: string) {
    SocketFake.opened.push(this);
  }

  send(data: unknown) {
    this.sent.push(data);
  }

  close() {}

  static last(): SocketFake {
    const socket = SocketFake.opened.at(-1);
    if (!socket) throw new Error("no shell socket was opened");
    return socket;
  }
}

async function openShell(): Promise<HTMLElement> {
  SocketFake.opened = [];
  vi.stubGlobal("WebSocket", SocketFake);
  wire({ ...transcript(), "/shell$": () => json({ available: true, active: true }) });
  open();
  await userEvent.click(await screen.findByRole("button", { name: "Shell, sandbox running" }));
  const terminal = await screen.findByTestId("shell");
  act(() => SocketFake.last().onopen?.());
  await waitFor(() => expect(terminal.textContent).toContain("Starting the sandbox"));
  return terminal;
}

test("the terminal keeps Escape while it is focused, and the sheet closes when the shell exits", async () => {
  const terminal = await openShell();
  expect(screen.queryByText("/workspace")).toBeNull();

  fireEvent.keyDown(terminal.querySelector("textarea") as HTMLTextAreaElement, {
    key: "Escape",
    keyCode: 27,
  });

  expect(screen.getByTestId("shell")).toBeTruthy();
  expect(Array.from(SocketFake.last().sent.at(-1) as Uint8Array)).toEqual([27]);

  act(() => SocketFake.last().onclose?.(new CloseEvent("close")));

  await waitFor(() => expect(screen.queryByTestId("shell")).toBeNull());
});

test("a shell that gets no sandbox says so in the terminal it was opened in", async () => {
  const terminal = await openShell();

  act(() =>
    SocketFake.last().onclose?.(
      new CloseEvent("close", { reason: "This conversation's sandbox is not reachable." }),
    ),
  );

  await waitFor(() =>
    expect(terminal.textContent).toContain("This conversation's sandbox is not reachable."),
  );
  expect(screen.getByTestId("shell")).toBeTruthy();
});

const RAN = "Digested the night's changes.";

function marked(): HTMLElement | null {
  return document.querySelector("[data-highlight]");
}

test("a conversation opened at a run stands on that run's words and marks them", async () => {
  const scrolled = vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(() => {});
  location.hash = chatHash(CONVO_ID, undefined, undefined, TURN_ID);
  wire(
    transcript({
      messages: [
        { role: "user", text: "Digest the night." },
        { role: "assistant", text: RAN, turn: TURN_ID },
        { role: "assistant", text: "A later turn.", turn: ARRIVAL_ID },
      ],
    }),
  );
  open();

  await screen.findByText(RAN);
  await waitFor(() => expect(marked()).not.toBeNull());
  expect(marked()!.textContent).toContain(RAN);
  expect(marked()!.textContent).not.toContain("A later turn.");
  expect(scrolled).toHaveBeenCalled();
});

test("a run the conversation no longer holds lands on the conversation with no mark and no notice", async () => {
  location.hash = chatHash(CONVO_ID, undefined, undefined, TURN_ID);
  wire(transcript({ messages: [{ role: "assistant", text: "What is left.", turn: ARRIVAL_ID }] }));
  open();

  await screen.findByText("What is left.");
  expect(marked()).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
});

test("the mark stands while the transcript settles under it, and clears on the member's next act", async () => {
  vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(() => {});
  const { rerender } = render(
    <TranscriptScroll>
      <MessageLog
        messages={[{ role: "assistant", text: RAN, turn: TURN_ID }]}
        focus={TURN_ID}
        live={liveTurn()}
      />
    </TranscriptScroll>,
  );

  expect(marked()!.textContent).toContain(RAN);

  rerender(
    <TranscriptScroll>
      <MessageLog
        messages={[
          { role: "assistant", text: RAN, turn: TURN_ID },
          { role: "assistant", text: "The next run.", turn: ARRIVAL_ID },
        ]}
        focus={TURN_ID}
      />
    </TranscriptScroll>,
  );

  expect(marked()!.textContent).toContain(RAN);

  fireEvent.pointerDown(document.body);

  await waitFor(() => expect(marked()).toBeNull());
  expect(screen.getByText(RAN)).toBeTruthy();
});

test("a run whose words a turn spoke twice is marked on the words that closed it", () => {
  vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(() => {});
  render(
    <TranscriptScroll>
      <MessageLog
        messages={[
          { role: "assistant", text: "Working on it.", turn: TURN_ID },
          { role: "user", text: "And the tags?" },
          { role: "assistant", text: RAN, turn: TURN_ID },
        ]}
        focus={TURN_ID}
      />
    </TranscriptScroll>,
  );

  expect(marked()!.textContent).toContain(RAN);
  expect(document.querySelectorAll("[data-highlight]")).toHaveLength(1);
});

const WOKE = "GitHub: 2 pages changed.";

test("a run whose turn wrote no reply is marked on the words that woke it", () => {
  vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(() => {});
  render(
    <TranscriptScroll>
      <MessageLog
        messages={[
          { role: "user", text: WOKE, turn: TURN_ID },
          { role: "user", text: "And this one?", turn: ARRIVAL_ID },
          { role: "assistant", text: "A later run.", turn: ARRIVAL_ID },
        ]}
        focus={TURN_ID}
      />
    </TranscriptScroll>,
  );

  expect(marked()!.textContent).toContain(WOKE);
  expect(document.querySelectorAll("[data-highlight]")).toHaveLength(1);
});

test("a run that wrote a reply is marked on the reply, not on the words that woke it", () => {
  vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(() => {});
  render(
    <TranscriptScroll>
      <MessageLog
        messages={[
          { role: "user", text: WOKE, turn: TURN_ID },
          { role: "assistant", text: RAN, turn: TURN_ID },
        ]}
        focus={TURN_ID}
      />
    </TranscriptScroll>,
  );

  expect(marked()!.textContent).toContain(RAN);
  expect(marked()!.textContent).not.toContain(WOKE);
});

test("the mark is the attention accent, never the fill a member's own words carry", () => {
  vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(() => {});
  render(
    <TranscriptScroll>
      <MessageLog messages={[{ role: "assistant", text: RAN, turn: TURN_ID }]} focus={TURN_ID} />
    </TranscriptScroll>,
  );

  const mark = marked()!.className;
  expect(mark).toContain("bg-attention");
  expect(mark).toContain("outline-attention-ink");
  expect(mark).not.toContain("bg-said");
  expect(mark).not.toContain("bg-affirm");
});

function markOnly() {
  return render(
    <TranscriptScroll>
      <MessageLog messages={[{ role: "assistant", text: RAN, turn: TURN_ID }]} focus={TURN_ID} />
    </TranscriptScroll>,
  );
}

test("the mark throbs once, holds, then fades and lets the words go", async () => {
  vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(() => {});
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    markOnly();

    expect(marked()!.className).toContain("animate-marked");
    expect(marked()!.getAttribute("data-letting-go")).toBeNull();

    await act(() => vi.advanceTimersByTimeAsync(2_200));

    expect(marked()!.getAttribute("data-letting-go")).toBe("true");
    expect(marked()!.className).not.toContain("animate-marked");
    expect(marked()!.className).toContain("bg-transparent");

    await act(() => vi.advanceTimersByTimeAsync(500));

    expect(marked()).toBeNull();
    expect(screen.getByText(RAN)).toBeTruthy();
  } finally {
    vi.useRealTimers();
  }
});

test("a member who asks for less motion gets the hold and the fade with no throb", async () => {
  vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(() => {});
  vi.stubGlobal("matchMedia", (media: string) => ({
    media,
    matches: media.includes("prefers-reduced-motion"),
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }));
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    markOnly();

    expect(marked()!.className).not.toContain("animate-marked");

    await act(() => vi.advanceTimersByTimeAsync(2_200));

    expect(marked()!.getAttribute("data-letting-go")).toBe("true");

    await act(() => vi.advanceTimersByTimeAsync(500));

    expect(marked()).toBeNull();
  } finally {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  }
});

test("a transcript settling under the mark neither restarts it nor holds it open", async () => {
  vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(() => {});
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    const { rerender } = render(
      <TranscriptScroll>
        <MessageLog
          messages={[{ role: "assistant", text: RAN, turn: TURN_ID }]}
          focus={TURN_ID}
          live={liveTurn()}
        />
      </TranscriptScroll>,
    );

    await act(() => vi.advanceTimersByTimeAsync(2_000));

    rerender(
      <TranscriptScroll>
        <MessageLog
          messages={[
            { role: "assistant", text: RAN, turn: TURN_ID },
            { role: "assistant", text: "The next run.", turn: ARRIVAL_ID },
          ]}
          focus={TURN_ID}
        />
      </TranscriptScroll>,
    );

    await act(() => vi.advanceTimersByTimeAsync(200));

    expect(marked()!.getAttribute("data-letting-go")).toBe("true");

    await act(() => vi.advanceTimersByTimeAsync(500));

    expect(marked()).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});

test("a reply landing for a marked run keeps the mark on the words it first stood on", async () => {
  vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(() => {});
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    const { rerender } = render(
      <TranscriptScroll>
        <MessageLog
          messages={[{ role: "user", text: WOKE, turn: TURN_ID }]}
          focus={TURN_ID}
          live={liveTurn()}
        />
      </TranscriptScroll>,
    );

    expect(marked()!.textContent).toContain(WOKE);

    await act(() => vi.advanceTimersByTimeAsync(1_000));

    rerender(
      <TranscriptScroll>
        <MessageLog
          messages={[
            { role: "user", text: WOKE, turn: TURN_ID },
            { role: "assistant", text: RAN, turn: TURN_ID },
          ]}
          focus={TURN_ID}
        />
      </TranscriptScroll>,
    );

    expect(marked()!.textContent).toContain(WOKE);
    expect(marked()!.textContent).not.toContain(RAN);
    expect(document.querySelectorAll("[data-highlight]")).toHaveLength(1);

    await act(() => vi.advanceTimersByTimeAsync(2_700));

    expect(marked()).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});

test("a run pressed while the transcript is still read is marked when its words land", async () => {
  vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(() => {});
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    const { rerender } = render(
      <TranscriptScroll>
        <MessageLog messages={[]} focus={TURN_ID} />
      </TranscriptScroll>,
    );

    await act(() => vi.advanceTimersByTimeAsync(4_000));

    rerender(
      <TranscriptScroll>
        <MessageLog messages={[{ role: "assistant", text: RAN, turn: TURN_ID }]} focus={TURN_ID} />
      </TranscriptScroll>,
    );

    expect(marked()!.textContent).toContain(RAN);
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();

    await act(() => vi.advanceTimersByTimeAsync(2_200));

    expect(marked()!.getAttribute("data-letting-go")).toBe("true");

    await act(() => vi.advanceTimersByTimeAsync(500));

    expect(marked()).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});

const OUT_OF_CREDIT = () => ({
  ...transcript(),
  "/api/agents/status": () => json({ statuses: [], out_of_credit: true }),
});

test("an out-of-credit workspace says so above the composer, and offers billing to an admin alone", async () => {
  wire(OUT_OF_CREDIT());
  render(<App agents={[AGENT]} member={{ ...MEMBER, admin: true }} onAgents={() => {}} />);

  expect(await screen.findByText("Out of credit. All work has stopped.")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Go to billing" }).getAttribute("href")).toBe(
    workspaceHash("billing"),
  );

  cleanup();
  wire(OUT_OF_CREDIT());
  open();

  expect(await screen.findByText("Out of credit. Ask your admin to add credit.")).toBeTruthy();
  expect(screen.queryByRole("link", { name: "Go to billing" })).toBeNull();
});

test("the credit line takes the eyebrow from the agent it addresses, and gives it back", async () => {
  wire(OUT_OF_CREDIT());
  render(<Chat agent={AGENT} member={MEMBER} conversationId={CONVO_ID} />);

  expect(await screen.findByText("Out of credit. Ask your admin to add credit.")).toBeTruthy();
  expect(screen.queryByText("Assistant")).toBeNull();

  /* The credit lands on the poll the mounted box already runs, so the line has to go on that
     answer — resetting the store would clear its listeners and prove nothing. */
  wire(transcript());
  await act(async () => {
    wakeAppStatus();
  });

  expect(await screen.findByText("Assistant")).toBeTruthy();
  expect(screen.queryByText(/^Out of credit\./)).toBeNull();
});
