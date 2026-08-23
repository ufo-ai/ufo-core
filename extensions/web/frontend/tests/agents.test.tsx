import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { Portal } from "@/Portal";
import { AGENT_ICONS } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";
import { chatState } from "@/lib/chatStore";
import { sendMessage } from "@/lib/turnStream";
import { WORKING_STATUS_MS, statusLine, type AgentStatus } from "@/views/Agents";

import {
  AGENT,
  AGENT_ID,
  CONVO_ID,
  FRESH,
  MEMBER,
  SECOND_ID,
  SETTINGS,
  StreamFake,
  TASK_KIND,
  TURN_ID,
  agentIndex,
  atPhoneWidth,
  json,
  objectIndex,
  openAgentRow,
  openAgentSettings,
  openRow,
  owned,
  saying,
  useStreamFake,
  wire,
} from "./harness";

const ADMIN = { ...MEMBER, admin: true };
const RESEARCH = {
  id: SECOND_ID,
  name: "research",
  model: "claude-opus-4-8",
  main: false,
  icon: "aten",
};
/** An app whose name a member typed as two lowercase words, which is the case the drawn name and
 *  the stored name differ in most plainly. */
const REVIEWER = {
  id: SECOND_ID,
  name: "code reviewer",
  model: "claude-opus-4-8",
  main: false,
  icon: "code",
};

/** An app that still needs a grant before it can work, as its own settings read states it. */
const NEEDS_SETUP = {
  ...SETTINGS,
  agent: {
    ...SETTINGS.agent,
    setup: { connectors: ["github"], instructions: "Connect the GitHub account." },
  },
};

/** The message the portal sends on the wizard's own behalf, so the run opens with the agent's
 *  proposal instead of an empty box. */
const OPENING = "Build me a new app.";
const TITLE = "Finances dash";

/** The phases the `create-application` skill opens a guided build's todo board with, as the
 *  conversation's `tasks` slot answers them. The progress bar reads nothing else. */
const PHASES = [
  "Propose what the app should do",
  "Ask what the build needs",
  "Design the homepage",
  "Create the app",
];

function board(done: number, running: number | null) {
  return json({
    type: "tasks",
    title: "App Builder",
    tasks: PHASES.map((description, index) => ({
      description,
      status: index < done ? "completed" : index === running ? "in_progress" : "pending",
    })),
    total_count: PHASES.length,
    completed_count: done,
    truncated: false,
  });
}

/** What the slot answers for a conversation whose agent has opened no board. */
const NO_BOARD = () =>
  json({ type: "tasks", title: "", tasks: [], total_count: 0, completed_count: 0, truncated: false });

function boot(agents: unknown[], member: unknown) {
  return json({ member, agents });
}

const OPENED = { turn_id: TURN_ID, conversation_id: CONVO_ID, title: TITLE };

const ASKED = {
  status: "done",
  model: "opus",
  tokens: 12,
  cost_micro_usd: 2_000_000,
};

async function raisedIndex(): Promise<HTMLElement> {
  if (!screen.queryByRole("navigation", { name: "Apps" })) {
    fireEvent.click(await screen.findByRole("button", { name: "Applications" }));
  }
  return agentIndex();
}

async function openWizard() {
  const index = await raisedIndex();
  await userEvent.click(within(index).getByRole("button", { name: "New application" }));
  return screen.findByRole("region", { name: "App Builder" });
}

function progress(): HTMLElement {
  return screen.getByRole("progressbar", { name: "App Builder" });
}

beforeEach(() => {
  location.hash = "";
  useStreamFake();
});

test("New application opens the wizard speaking in the pane, and the flyout keeps its apps", async () => {
  const sent: { url: string; body: string }[] = [];
  wire({
    "/api/agents": () => boot([AGENT], ADMIN),
    "/chat": (url, init) => {
      sent.push({ url, body: String(init?.body) });
      return json(OPENED);
    },
    "/slots/tasks": NO_BOARD,
    "/transcript": () => json({ messages: [] }),
  });
  render(<Portal />);

  const wizard = await openWizard();

  await waitFor(() => expect(sent.length).toBe(1));
  expect(sent[0].url).toBe("/surface/web/agents/" + AGENT_ID + "/chat?conversation=new");
  expect(sent[0].body).toBe(OPENING);
  expect(within(wizard).getByText(OPENING)).toBeTruthy();
  expect(within(wizard).getByLabelText("Message the app")).toBeTruthy();
  expect(within(await raisedIndex()).getByText("Assistant")).toBeTruthy();

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  expect(StreamFake.last().url).toBe("/surface/web/turns/" + TURN_ID + "/stream");
  StreamFake.last().emit("message", { text: "A finances dashboard, then." });
  expect(await screen.findByText(saying("A finances dashboard, then."))).toBeTruthy();
});

test("the opening message founds one conversation however many times the wizard mounts", async () => {
  const sent: string[] = [];
  wire({
    "/api/agents": () => boot([AGENT], ADMIN),
    "/chat": (_url, init) => {
      sent.push(String(init?.body));
      return json(OPENED);
    },
    "/slots/tasks": NO_BOARD,
    "/transcript": () => json({ messages: [] }),
  });
  render(
    <StrictMode>
      <Portal />
    </StrictMode>,
  );

  await openWizard();

  await waitFor(() => expect(sent).toEqual([OPENING]));
  expect(StreamFake.opened.length).toBe(1);
});

test("an app page draws its homepage from the boot agent, without pulling its conversation index", async () => {
  const SITE = { state: "set", url: "https://ingress.test/site", deploy_generation: 3 };
  const RADAR = {
    ...AGENT,
    id: SECOND_ID,
    name: "radar",
    main: false,
    app: "radar",
    icon: "aten",
    homepage: SITE,
  };
  const { calls } = wire({
    "/api/agents": () => boot([AGENT, RADAR], ADMIN),
    "/homepage": () => json(SITE),
  });
  render(<Portal />);
  location.hash = "#/agents/" + SECOND_ID;

  // The page rides the agent from the boot read — the frame carries boot's own url — so the pane
  // does not pull the app's conversation index for a set page with nothing open.
  const region = await screen.findByRole("region", { name: /radar homepage/i });
  expect(region.querySelector("iframe")?.getAttribute("src")).toBe("https://ingress.test/site");
  expect(calls.some((url) => url.includes("/agents/" + SECOND_ID + "/conversations"))).toBe(false);
});

/** The opening send is not the only send that can found the run's conversation: when it fails, the
 *  member's own next message founds one, and the wizard has to be bound to that conversation or the
 *  pane keeps reading a store key the founding send already migrated away from. */
test("a conversation the composer founds after a failed opening send is still the run's", async () => {
  let opening = true;
  wire({
    "/api/agents": () => boot([AGENT], ADMIN),
    "/chat": () => {
      if (opening) {
        opening = false;
        return new Response("nope", { status: 503 });
      }
      return json(OPENED);
    },
    "/slots/tasks": () => board(1, 1),
    "/transcript": () => json({ messages: [] }),
  });
  render(<Portal />);

  const wizard = await openWizard();
  expect(await within(wizard).findByText("Error 503 — try again.")).toBeTruthy();

  await userEvent.type(within(wizard).getByLabelText("Message the app"), "A finances dashboard.");
  await userEvent.click(within(wizard).getByRole("button", { name: "Send" }));

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  expect(within(wizard).getByText(saying("A finances dashboard."))).toBeTruthy();
  // The bar reads the founded conversation's own board, and the rail row takes its title — neither
  // is reachable from a wizard that lost the conversation.
  expect(await waitFor(progress)).toBeTruthy();
  expect(within(await raisedIndex()).getByText("App Builder: " + TITLE)).toBeTruthy();
});

test("the rail names the run in flight and takes the conversation's own title", async () => {
  let answer = () => {};
  const opening = new Promise<void>((resolve) => {
    answer = resolve;
  });
  wire({
    "/api/agents": () => boot([AGENT], ADMIN),
    "/chat": async () => {
      await opening;
      return json(OPENED);
    },
    "/slots/tasks": NO_BOARD,
    "/transcript": () => json({ messages: [] }),
  });
  render(<Portal />);

  const index = await raisedIndex();
  expect(within(index).queryByText("App Builder")).toBeNull();

  await userEvent.click(within(index).getByRole("button", { name: "New application" }));

  // Until admission answers there is no conversation and nothing to call it, so the row states the
  // run alone; the title the chat route hands back names it from then on.
  const raised = await raisedIndex();
  expect(await within(raised).findByText("App Builder")).toBeTruthy();
  expect(within(raised).queryByRole("button", { name: /App Builder/ })).toBeNull();

  answer();

  expect(await within(raised).findByText("App Builder: " + TITLE)).toBeTruthy();
});

test("the progress bar reads the run's own phase board and advances with it", async () => {
  let phase = 0;
  wire({
    "/api/agents": () => boot([AGENT], ADMIN),
    "/chat": () => json(OPENED),
    "/slots/tasks": () => board(phase, phase),
    "/transcript": () => json({ messages: [] }),
  });
  render(<Portal />);

  await openWizard();
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));

  const bar = await waitFor(progress);
  expect(bar.getAttribute("aria-valuenow")).toBe("0");
  expect(bar.getAttribute("aria-valuemax")).toBe("4");
  expect(screen.getByText(PHASES[0])).toBeTruthy();
  expect(screen.getByText("1 of 4")).toBeTruthy();

  phase = 2;
  StreamFake.last().emit("terminal", {
    ...ASKED,
    question: {
      title: "Two more things.",
      questions: [{ question: "How careful should it be?", options: [{ label: "medium" }] }],
    },
  });

  await waitFor(() => expect(progress().getAttribute("aria-valuenow")).toBe("2"));
  expect(await screen.findByText(PHASES[2])).toBeTruthy();
  expect(screen.getByText("3 of 4")).toBeTruthy();
});

test("a run whose agent kept no board draws no bar", async () => {
  wire({
    "/api/agents": () => boot([AGENT], ADMIN),
    "/chat": () => json(OPENED),
    "/slots/tasks": NO_BOARD,
    "/transcript": () => json({ messages: [] }),
  });
  render(<Portal />);

  await openWizard();

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  expect(screen.queryByRole("progressbar")).toBeNull();
});

test("an answer to the wizard's question rides the turn that asked it", async () => {
  const answers: { turn: string; index: string; body: string }[] = [];
  wire({
    "/api/agents": () => boot([AGENT], ADMIN),
    "/chat": (_url, init) => {
      const headers = (init?.headers ?? {}) as Record<string, string>;
      const turn = headers["x-ufo-answer-turn"];
      if (turn) {
        answers.push({ turn, index: headers["x-ufo-answer-question"], body: String(init?.body) });
      }
      return json(OPENED);
    },
    "/slots/tasks": () => board(1, 1),
    "/transcript": () => json({ messages: [] }),
  });
  render(<Portal />);

  await openWizard();
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  // The question rides the turn's terminal, and a member can only answer in a pane that has drawn
  // the conversation — so the emit waits for the bind the rail's title states, the way a real
  // terminal can only follow a stream the page already tails.
  expect(await within(await raisedIndex()).findByText("App Builder: " + TITLE)).toBeTruthy();
  StreamFake.last().emit("terminal", {
    ...ASKED,
    question: {
      title: "Two more things.",
      questions: [
        {
          question: "Who reaches it?",
          options: [{ label: "The whole workspace" }, { label: "Just me" }],
        },
      ],
    },
  });

  await userEvent.click(await screen.findByRole("radio", { name: /The whole workspace/ }));
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));

  await waitFor(() => expect(answers.length).toBe(1));
  expect(answers[0]).toEqual({ turn: TURN_ID, index: "0", body: "The whole workspace" });
});

test("the app the last phase creates reaches the rail when the turn settles", async () => {
  let landed = false;
  wire({
    "/api/agents": () => boot(landed ? [AGENT, RESEARCH] : [AGENT], ADMIN),
    "/chat": () => json(OPENED),
    "/slots/tasks": () => board(landed ? 5 : 4, landed ? null : 4),
    "/transcript": () => json({ messages: [] }),
    "/homepage": () => json({ state: "none" }),
  });
  render(<Portal />);

  await openWizard();
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));

  landed = true;
  StreamFake.last().emit("terminal", { ...ASKED, text: "Created research." });

  await waitFor(() => expect(progress().getAttribute("aria-valuenow")).toBe("5"));
  await userEvent.click(screen.getByRole("button", { name: "Close" }));
  await raisedIndex();
  await openAgentRow("Research");

  expect(location.hash).toBe("#/agents/" + SECOND_ID);
  expect(screen.queryByText("No such app.")).toBeNull();
  expect(await screen.findByRole("region", { name: "Research" })).toBeTruthy();
});

test("the New application act stands under the app rows, at the foot of the index", async () => {
  location.hash = "#/agents";
  wire({ "/api/agents": () => boot([AGENT, RESEARCH], ADMIN) });
  render(<Portal />);

  const index = await raisedIndex();
  const act = within(index).getByRole("button", { name: "New application" });
  const last = within(index).getByRole("button", { name: /^Research/ });

  expect(last.compareDocumentPosition(act) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

/** A phone screen has no width for a column beside the page, so the nav drawer holds the index
 *  there and the app's own pane is the page — the main app's until the member picks another. */
test("the drawer holds the apps index at a phone width, and a pick shuts it", async () => {
  atPhoneWidth();
  location.hash = "#/agents";
  wire({ "/api/agents": () => boot([AGENT, RESEARCH], ADMIN) });
  render(<Portal />);

  expect(await screen.findByRole("region", { name: "Assistant" })).toBeTruthy();
  expect(screen.queryByRole("navigation", { name: "Apps" })).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Menu" }));
  const drawer = await screen.findByRole("dialog");
  const index = await raisedIndex();
  expect(drawer.contains(index)).toBe(true);
  await userEvent.click(within(index).getByRole("button", { name: /^Research/ }));

  expect(location.hash).toBe("#/agents/" + SECOND_ID);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(await screen.findByRole("region", { name: "Research" })).toBeTruthy();
});

test("closing the wizard gives the pane back to the app and clears the run's row", async () => {
  wire({
    "/api/agents": () => boot([AGENT], ADMIN),
    "/chat": () => json(OPENED),
    "/slots/tasks": NO_BOARD,
    "/transcript": () => json({ messages: [] }),
    "/homepage": () => json({ state: "none" }),
  });
  render(<Portal />);

  await openWizard();
  await userEvent.click(screen.getByRole("button", { name: "Close" }));

  await waitFor(() => expect(screen.queryByRole("region", { name: "App Builder" })).toBeNull());
  expect(within(await raisedIndex()).queryByText("App Builder")).toBeNull();
  expect(await screen.findByRole("region", { name: "Assistant" })).toBeTruthy();
});

/** The pane and its Close button stand open while the founding send is in flight, so that send's own
 *  callback can land after the member closed the run. It must not raise the run again: the pane it
 *  brought back would send the opening message a second time, and the member would hold a second
 *  app-building conversation and a second billed turn. */
test("a close before the founding send answers keeps the pane closed and founds one conversation", async () => {
  let answer = () => {};
  const opening = new Promise<void>((resolve) => {
    answer = resolve;
  });
  const sent: string[] = [];
  wire({
    "/api/agents": () => boot([AGENT], ADMIN),
    "/chat": async (_url, init) => {
      sent.push(String(init?.body));
      await opening;
      return json(OPENED);
    },
    "/slots/tasks": NO_BOARD,
    "/transcript": () => json({ messages: [] }),
    "/homepage": () => json({ state: "none" }),
  });
  render(<Portal />);

  await openWizard();
  await waitFor(() => expect(sent).toEqual([OPENING]));
  await userEvent.click(screen.getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByRole("region", { name: "App Builder" })).toBeNull());

  answer();

  // The conversation the closed run founded tails to its own end; nothing of it reaches the screen
  // the member went back to.
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  expect(sent).toEqual([OPENING]);
  expect(screen.queryByRole("region", { name: "App Builder" })).toBeNull();
  expect(within(await raisedIndex()).queryByText(/App Builder/)).toBeNull();
});

/** The run is the key's, not the mount's: a wizard reopened while its founding send is still in
 *  flight joins that run when it lands, instead of founding a second conversation or losing both. */
test("a wizard reopened during its founding send binds to the run the first mount opened", async () => {
  let answer = () => {};
  const opening = new Promise<void>((resolve) => {
    answer = resolve;
  });
  const sent: string[] = [];
  wire({
    "/api/agents": () => boot([AGENT], ADMIN),
    "/chat": async (_url, init) => {
      sent.push(String(init?.body));
      await opening;
      return json(OPENED);
    },
    "/slots/tasks": NO_BOARD,
    "/transcript": () => json({ messages: [] }),
    "/homepage": () => json({ state: "none" }),
  });
  render(<Portal />);

  await openWizard();
  await waitFor(() => expect(sent).toEqual([OPENING]));
  await userEvent.click(screen.getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByRole("region", { name: "App Builder" })).toBeNull());

  const wizard = await openWizard();
  answer();

  await waitFor(() =>
    expect(within(wizard).getByText(saying(OPENING)) || within(wizard).getByText(OPENING)).toBeTruthy(),
  );
  expect(sent).toEqual([OPENING]);
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  expect(within(await raisedIndex()).getByText("App Builder: " + TITLE)).toBeTruthy();
});

/** The run is the key's, not the screen's: leaving for another screen unmounts every pane here,
 *  and what the member finds on coming back is the same run, not a second opening send. */
test("the run survives leaving the screen and comes back bound, sending nothing twice", async () => {
  const sent: string[] = [];
  wire({
    "/api/agents": () => boot([AGENT], ADMIN),
    "/chat": (_url, init) => {
      sent.push(String(init?.body));
      return json(OPENED);
    },
    "/slots/tasks": NO_BOARD,
    "/transcript": () => json({ messages: [] }),
  });
  render(<Portal />);

  await openWizard();
  await waitFor(() => expect(sent).toEqual([OPENING]));

  await userEvent.click(screen.getByRole("button", { name: "New conversation" }));
  await waitFor(() => expect(screen.queryByRole("region", { name: "App Builder" })).toBeNull());
  const index = await raisedIndex();
  await userEvent.click(within(index).getByRole("button", { name: /App Builder/ }));

  const wizard = await screen.findByRole("region", { name: "App Builder" });
  expect(await within(wizard).findByText(saying(OPENING))).toBeTruthy();
  expect(await within(await raisedIndex()).findByText("App Builder: " + TITLE)).toBeTruthy();
  expect(sent).toEqual([OPENING]);
});

/** An app row shows its app without ending the run: the rail keeps naming the run, and its row —
 *  now a place to go rather than the place the member is — takes them back to it. */
test("an app row leaves the run standing, and the rail's own row returns to it", async () => {
  const sent: string[] = [];
  wire({
    "/api/agents": () => boot([AGENT, RESEARCH], ADMIN),
    "/chat": (_url, init) => {
      sent.push(String(init?.body));
      return json(OPENED);
    },
    "/slots/tasks": NO_BOARD,
    "/transcript": () => json({ messages: [] }),
    "/homepage": () => json({ state: "none" }),
  });
  render(<Portal />);

  await openWizard();
  await waitFor(() => expect(sent).toEqual([OPENING]));

  await raisedIndex();
  await openAgentRow("Research");
  await waitFor(() => expect(screen.queryByRole("region", { name: "App Builder" })).toBeNull());
  const index = await raisedIndex();
  const row = await within(index).findByRole("button", { name: /App Builder/ });

  await userEvent.click(row);
  expect(await screen.findByRole("region", { name: "App Builder" })).toBeTruthy();
  expect(sent).toEqual([OPENING]);
});

/** The wizard founds on a key of its own, so a founding send the member has in flight on the chat
 *  screen cannot hold it busy — the failure class three review rounds circled. */
test("a founding send from the chat screen never blocks the wizard's own", async () => {
  let answerChat = () => {};
  const chatOpening = new Promise<void>((resolve) => {
    answerChat = resolve;
  });
  const sent: string[] = [];
  wire({
    "/api/agents": () => boot([AGENT], ADMIN),
    "/chat": async (_url, init) => {
      const body = String(init?.body);
      sent.push(body);
      if (body !== OPENING) {
        await chatOpening;
        return json({ turn_id: "turn-2", conversation_id: "convo-2", title: "Numbers" });
      }
      return json(OPENED);
    },
    "/slots/tasks": NO_BOARD,
    "/transcript": () => json({ messages: [] }),
    "/homepage": () => json({ state: "none" }),
  });
  render(<Portal />);

  await userEvent.type(await screen.findByLabelText("Message the app"), "About our numbers.");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(sent).toEqual(["About our numbers."]));

  const wizard = await openWizard();
  await waitFor(() => expect(sent).toEqual(["About our numbers.", OPENING]));
  expect(within(wizard).getByText(OPENING)).toBeTruthy();

  answerChat();
  await waitFor(() => expect(within(wizard).queryByText("About our numbers.")).toBeNull());
  expect(within(await raisedIndex()).getByText("App Builder: " + TITLE)).toBeTruthy();
});

/** A founding send the key turns away is refused to its caller and worn by the key as a fault —
 *  never a silent drop the member reads as words that went somewhere. */
test("a second founding send on a busy key is refused and the key wears the fault", async () => {
  wire({
    "/chat": () => new Promise<Response>(() => {}),
  });
  const target = { key: "new:" + AGENT_ID, agentId: AGENT_ID, conversationId: null };
  const first = sendMessage(target, "One.", "One.");
  expect(await sendMessage(target, "Two.", "Two.")).toBe("refused");
  expect(chatState("new:" + AGENT_ID).fault?.title).toBe("The conversation is still opening.");
  expect(chatState("new:" + AGENT_ID).messages?.map((message) => message.text)).toEqual(["One."]);
  void first;
});

/** The act is offered to every member the screen draws, admin or not: the `agent` kind admits a
 *  create from any speaking member and stamps them the owner. */
test("a member who is no admin is offered the act, and it opens the wizard", async () => {
  wire({
    "/api/agents": () => boot([AGENT], MEMBER),
    "/chat": () => json(OPENED),
    "/slots/tasks": NO_BOARD,
    "/transcript": () => json({ messages: [] }),
  });
  render(<Portal />);

  expect(await openWizard()).toBeTruthy();
});

test("each row in the index draws its own app's mark, and states nothing by it", async () => {
  wire({ "/transcript": () => json({ messages: [] }) });
  render(<App agents={[AGENT, RESEARCH]} member={MEMBER} onAgents={() => {}} />);

  const index = within(await raisedIndex());

  const assistant = index.getByRole("button", { name: /^Assistant/ });
  expect(assistant.querySelector(".element-icon-propylon")).toBeTruthy();
  expect(assistant.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
  expect(index.getByRole("button", { name: /^Research/ }).querySelector(".element-icon-aten"))
    .toBeTruthy();
});

/** The out-of-shortlist test walks the object's own properties, never the prototype's: a slug
 *  every object answers — `constructor` — would otherwise read as bundled, and the picker would
 *  open with the agent's own mark neither led with nor checked. */
test("a mark named for what every object answers still leads the picker as the agent's own", async () => {
  wire({
    "/settings": () => json({ ...SETTINGS, spec: { ...SETTINGS.spec, icon: "constructor" } }),
    "/connections": () => json({ connections: [] }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await openAgentSettings();

  const marks = await screen.findAllByRole("radio");
  expect(marks.length).toBe(Object.keys(AGENT_ICONS).length + 1);
  expect(marks[0].getAttribute("value")).toBe("constructor");
  expect((marks[0] as HTMLInputElement).checked).toBe(true);
});

test("a slot the settings dialog raises stands inside it, beside what raised it", async () => {
  wire({
    "/settings": () => json(SETTINGS),
    "/connections": () => json({ connections: [] }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  const dialog = await openAgentSettings("Assistant", "Connectors");

  await userEvent.click(within(dialog).getByRole("button", { name: "Add connector" }));

  const form = await screen.findByRole("region", { name: "Add connector" });
  expect(dialog.contains(form)).toBe(true);
  const cover = form.closest("[data-slot=slot-track]");
  expect(cover?.className).toContain("absolute");
  expect(cover?.contains(within(dialog).getByRole("tabpanel"))).toBe(false);
});

/** The dialog holds no address, so it holds the one track its Scheduled tab walks. The index there
 *  is the root like any other: a second row takes the first record's place, and the dialog reopens
 *  on the first tab with nothing standing. */
test("a second row of the dialog's Scheduled tab takes the first record's place", async () => {
  const task = (name: string, prompt: string) =>
    json({
      ...TASK_KIND,
      name,
      summary: "0 9 * * * — " + prompt,
      spec: { schedule: "0 9 * * *", prompt, paused: false },
      status: { next_run_at: "2026-08-15T09:00:00+00:00", paused: false },
      links: [],
      created_at: "2026-08-01T09:00:00Z",
      updated_at: "2026-08-01T09:00:00Z",
    });
  wire({
    "/settings": () => json(SETTINGS),
    "/connections": () => json({ connections: [] }),
    "/objects/scheduled_task/daily-brief": () => task("daily-brief", "write the daily brief"),
    "/objects/scheduled_task/weekly-roll": () => task("weekly-roll", "roll up the week"),
    "/objects/scheduled_task": () =>
      objectIndex(TASK_KIND, [
        owned({ name: "daily-brief", summary: "0 9 * * * — daily brief", paused: false }),
        owned({ name: "weekly-roll", summary: "0 9 * * 1 — weekly roll-up", paused: false }),
      ]),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  const dialog = await openAgentSettings("Assistant", "Scheduled");
  const standing = () =>
    within(dialog)
      .queryAllByRole("region")
      .map((slot) => slot.getAttribute("aria-label"));

  await openRow("daily-brief");
  expect(await within(dialog).findByRole("region", { name: "daily-brief" })).toBeTruthy();

  await openRow("weekly-roll");

  expect(await within(dialog).findByRole("region", { name: "weekly-roll" })).toBeTruthy();
  await waitFor(() => expect(standing()).toEqual(["weekly-roll"]));

  await userEvent.click(within(dialog).getByRole("tab", { name: "Settings" }));
  await userEvent.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

  const reopened = await openAgentSettings("Assistant", "Scheduled");
  expect(await within(reopened).findByText("daily-brief")).toBeTruthy();
  expect(within(reopened).queryAllByRole("region")).toEqual([]);
});

/** The workspace's own mark is reserved: the picker offers it to no app, and the main agent is the
 *  row that holds it. It appears there once and only as that row's own mark — led, checked and drawn
 *  as the brand's own mark — the way any mark from outside the offered set appears. */
test("the main agent keeps the reserved mark, and no offered cell repeats it", async () => {
  wire({
    "/settings": () => json({ ...SETTINGS, spec: { ...SETTINGS.spec, icon: "ufo" } }),
    "/connections": () => json({ connections: [] }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[{ ...AGENT, icon: "ufo" }]} member={MEMBER} onAgents={() => {}} />);
  await openAgentSettings();

  const marks = await screen.findAllByRole("radio");
  expect(marks.length).toBe(Object.keys(AGENT_ICONS).length + 1);
  expect(marks[0].getAttribute("value")).toBe("ufo");
  expect(marks[0].getAttribute("aria-label")).toBe("ufo");
  expect((marks[0] as HTMLInputElement).checked).toBe(true);
  expect(marks[0].closest("label")!.querySelector(".brand-mark")).toBeTruthy();
  expect(marks.slice(1).map((mark) => mark.getAttribute("value"))).not.toContain("ufo");
});

test("a member picks another mark, and the pick rides one intent and comes back", async () => {
  const posted: unknown[] = [];
  let icon = "propylon";
  wire({
    "/settings": () => json({ ...SETTINGS, spec: { ...SETTINGS.spec, icon } }),
    "/connections": () => json({ connections: [] }),
    "/intents": (_url, init) => {
      const body = JSON.parse(String(init?.body));
      posted.push(body);
      icon = String(body.spec.icon);
      return json({ applied: true, message: "Applied." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await openAgentSettings();

  const marks = await screen.findAllByRole("radio");
  expect(marks.length).toBe(Object.keys(AGENT_ICONS).length);
  // The picker draws the offered set in its own order, and the product's own mark is not in it:
  // the workspace's mark is reserved, so no picker offers it to an app.
  expect(Object.keys(AGENT_ICONS)[0]).toBe("propylon");
  expect(marks[0].getAttribute("value")).toBe("propylon");
  expect(marks.map((mark) => mark.getAttribute("value"))).not.toContain("ufo");
  expect(screen.queryByRole("radio", { name: "ufo" })).toBeNull();
  // Every label is the slug's words capitalized.
  expect(marks[0].getAttribute("aria-label")).toBe("Propylon");
  expect(screen.getByRole("radio", { name: "Kylix" })).toBeTruthy();
  expect((screen.getByRole("radio", { name: "Propylon" }) as HTMLInputElement).checked).toBe(
    true,
  );
  // The schema hides `icon` the way it hides `prompt`, so the generic form draws no control for it.
  expect(screen.queryByRole("combobox", { name: "icon" })).toBeNull();
  expect(screen.queryByRole("textbox", { name: "icon" })).toBeNull();

  await userEvent.click(screen.getByRole("radio", { name: "Krepis" }));
  await userEvent.click(screen.getByRole("button", { name: "Save icon" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toEqual({
    verb: "apply",
    kind: "agent",
    name: "assistant",
    spec: { icon: "krepis" },
  });
  expect(await screen.findByText("Applied.")).toBeTruthy();
  await waitFor(() =>
    expect((screen.getByRole("radio", { name: "Krepis" }) as HTMLInputElement).checked).toBe(
      true,
    ),
  );
  expect((screen.getByRole("radio", { name: "Propylon" }) as HTMLInputElement).checked).toBe(
    false,
  );
});

const THIRD_ID = "44444444-4444-4444-8444-444444444444";
const FOURTH_ID = "66666666-6666-4666-8666-666666666666";
const SCRIBE = { id: THIRD_ID, name: "scribe", model: "auto", main: false, icon: "kylix" };
const WATCHER = { id: FOURTH_ID, name: "watcher", model: "auto", main: false, icon: "dingir" };

const HOURS_AGO = new Date(Date.now() - 5 * 3_600_000).toISOString();
const IN_THREE_HOURS = new Date(Date.now() + 3 * 3_600_000).toISOString();

function status(agentId: string, held: Partial<AgentStatus>): AgentStatus {
  return {
    agent_id: agentId,
    turn: null,
    activity: null,
    next_run_at: null,
    last_active_at: null,
    last_failed: false,
    ...held,
  };
}

test("the index sorts a working app as now, a resting one by its last activity, an unplaced one last", async () => {
  wire({
    "/api/agents/status": () =>
      json({
        statuses: [
          status(SECOND_ID, { turn: "running" }),
          status(AGENT_ID, { last_active_at: HOURS_AGO }),
        ],
      }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents";
  render(<App agents={[AGENT, RESEARCH, SCRIBE]} member={MEMBER} onAgents={() => {}} />);

  const index = await raisedIndex();
  await waitFor(() => {
    const rows = within(index).getAllByRole("button", { name: /^(Assistant|Research|Scribe)/ });
    expect(rows.map((row) => row.querySelector(".text-label")!.textContent)).toEqual([
      "Research",
      "Assistant",
      "Scribe",
    ]);
  });
});

test("a working row prints its work under the name, in plain text and with no tooltip", async () => {
  wire({
    "/api/agents/status": () =>
      json({
        statuses: [
          status(SECOND_ID, { turn: "running", activity: "Running bash" }),
          status(THIRD_ID, { turn: "running" }),
          status(FOURTH_ID, { turn: "queued" }),
        ],
      }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents";
  render(
    <App agents={[AGENT, RESEARCH, SCRIBE, WATCHER]} member={MEMBER} onAgents={() => {}} />,
  );

  const index = await raisedIndex();
  const row = (name: RegExp) => within(index).getByRole("button", { name });
  // The line is drawn a character at a time, so what a reader is given is the whole line beside
  // those cells and the cells themselves are hidden — which is what the row is named by.
  await waitFor(() => expect(row(/^Research Running bash$/)).toBeTruthy());
  expect(row(/^Scribe Responding$/)).toBeTruthy();
  expect(row(/^Watcher Queued$/)).toBeTruthy();
  const said = row(/^Research/).querySelector(".font-mono")!;
  expect(said.className).toContain("text-small");
  expect(said.querySelector(".sr-only")!.textContent).toBe("Running bash");
  expect(said.querySelector("[aria-hidden]")!.textContent).toBe("Running bash");
  // The line is already on the row, so nothing is held at the pointer over it.
  fireEvent.focus(row(/^Research/));
  expect(screen.queryByRole("tooltip")).toBeNull();
});

test("a resting row holds its status at the pointer, and one without a status triggers no tooltip", async () => {
  wire({
    "/api/agents/status": () =>
      json({ statuses: [status(SECOND_ID, { last_active_at: HOURS_AGO })] }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents";
  render(<App agents={[AGENT, RESEARCH]} member={MEMBER} onAgents={() => {}} />);

  const index = await raisedIndex();
  const row = () => within(index).getByRole("button", { name: /^Research/ });
  fireEvent.focus(row());
  expect((await screen.findByRole("tooltip")).textContent).toBe("Active 5h ago");
  fireEvent.blur(row());

  // An app the read said nothing about has nothing to hold there, so nothing opens over it.
  fireEvent.focus(within(index).getByRole("button", { name: /^Assistant/ }));
  await waitFor(() => expect(screen.queryByRole("tooltip")).toBeNull());
});

test("the avatar dot reads live on work in flight, blocked on parked or failed, and idle wears none", async () => {
  wire({
    "/api/agents/status": () =>
      json({
        statuses: [
          status(AGENT_ID, { turn: "running" }),
          status(SECOND_ID, { turn: "parked" }),
          status(THIRD_ID, { last_failed: true }),
          status(FOURTH_ID, { last_active_at: HOURS_AGO }),
        ],
      }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents";
  render(
    <App agents={[AGENT, RESEARCH, SCRIBE, WATCHER]} member={MEMBER} onAgents={() => {}} />,
  );

  const index = await raisedIndex();
  const dot = (name: RegExp, tone: string) =>
    within(index).getByRole("button", { name }).querySelector("." + tone);
  await waitFor(() => expect(dot(/^Assistant/, "bg-live")).toBeTruthy());
  expect(dot(/^Research/, "bg-blocked")).toBeTruthy();
  expect(dot(/^Scribe/, "bg-blocked")).toBeTruthy();
  expect(dot(/^Watcher/, "bg-live")).toBeNull();
  expect(dot(/^Watcher/, "bg-blocked")).toBeNull();
});

test("a row keeps the member's focus as its status gains and loses a tooltip", async () => {
  let working = true;
  wire({
    "/api/agents/status": () =>
      json({
        statuses: [
          working
            ? status(SECOND_ID, { turn: "running" })
            : status(SECOND_ID, { last_active_at: HOURS_AGO }),
        ],
      }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents";
  render(<App agents={[AGENT, RESEARCH]} member={MEMBER} onAgents={() => {}} />);

  const index = await raisedIndex();
  const row = () => within(index).getByRole("button", { name: /^Research/ });
  await waitFor(() => expect(row().querySelector(".bg-live")).toBeTruthy());
  row().focus();
  expect(document.activeElement).toBe(row());

  // The work ends, the row loses its line and gains a tooltip: the same element wears both, so
  // whoever was standing on it still is.
  working = false;
  await waitFor(() => expect(row().querySelector(".bg-live")).toBeNull(), {
    timeout: 2 * WORKING_STATUS_MS,
  });
  expect(document.activeElement).toBe(row());
  expect((await screen.findByRole("tooltip")).textContent).toBe("Active 5h ago");
}, 15_000);

/** A presence read is nobody's errand: no member action re-runs it, so a poll that gave up on one
 *  refusal would leave the rail stating the workspace had gone quiet until the screen was left. */
test("a status read that fails after answering keeps polling and recovers", async () => {
  let answers = 0;
  wire({
    "/api/agents/status": () => {
      answers += 1;
      if (answers === 2) return new Response("nope", { status: 502 });
      return json({ statuses: [status(SECOND_ID, { turn: "running" })] });
    },
    "/transcript": () => json({ messages: [] }),
  });
  // Off the apps pane, so the flyout is the only reader of the status route — the poll under test
  // is its own, not one the pane also runs.
  location.hash = "";
  vi.useFakeTimers();
  try {
    render(<App agents={[AGENT, RESEARCH]} member={MEMBER} onAgents={() => {}} />);
    const settle = async (ms: number) => {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(ms);
        await Promise.resolve();
        await Promise.resolve();
      });
    };
    await settle(0);
    fireEvent.click(screen.getByRole("button", { name: "Applications" }));
    await settle(0);
    const row = () => screen.getByRole("button", { name: /^Research/ });
    expect(row().querySelector(".bg-live")).toBeTruthy();

    await settle(WORKING_STATUS_MS);
    expect(answers).toBe(2);
    expect(row().querySelector(".bg-live")).toBeNull();

    await settle(WORKING_STATUS_MS);
    expect(answers).toBe(3);
    expect(row().querySelector(".bg-live")).toBeTruthy();
  } finally {
    vi.useRealTimers();
  }
});

test("statusLine states each resting mark by its own words", () => {
  expect(statusLine(undefined)).toBeNull();
  expect(statusLine(status(AGENT_ID, { turn: "parked" }))).toBe("Paused");
  expect(statusLine(status(AGENT_ID, { last_failed: true }))).toBe("Last run failed");
  expect(statusLine(status(AGENT_ID, { next_run_at: IN_THREE_HOURS }))).toBe("Next run in 3h");
  expect(statusLine(status(AGENT_ID, { last_active_at: HOURS_AGO }))).toBe("Active 5h ago");
  expect(statusLine(status(AGENT_ID, {}))).toBe("Idle");
});

test("a name is drawn word by word, and only a word written wholly in lowercase is raised", () => {
  expect(agentName("assistant")).toBe("Assistant");
  expect(agentName("code reviewer")).toBe("Code Reviewer");
  expect(agentName("Code reviewer")).toBe("Code Reviewer");
  expect(agentName("iOS helper")).toBe("iOS Helper");
  // A hyphen parts words the way a space does, so an extension's slug is not left half-drawn.
  expect(agentName("daily-brief")).toBe("Daily-Brief");
  expect(agentName("release_bot")).toBe("Release_Bot");
});

test("the index row and the pane header draw the app's name in Title Case", async () => {
  wire({ "/transcript": () => json({ messages: [] }) });
  location.hash = "#/agents/" + SECOND_ID;
  render(<App agents={[AGENT, REVIEWER]} member={MEMBER} onAgents={() => {}} />);

  const index = within(await raisedIndex());
  expect(index.getByRole("button", { name: /^Code Reviewer/ })).toBeTruthy();
  expect(index.queryByText("code reviewer")).toBeNull();

  const pane = within(await screen.findByRole("region", { name: "Code Reviewer" }));
  expect(pane.getByRole("button", { name: "Settings for Code Reviewer" })).toBeTruthy();
  expect(pane.queryByText("code reviewer")).toBeNull();
});

/** The drawn name is text; the stored name is the app's identity, and it is what addresses the
 *  object in the intent. A screen that raised the name it writes would rename the app on its first
 *  save. */
test("the settings dialog reads the drawn name and the intent it posts carries the stored one", async () => {
  const posted: { name: string }[] = [];
  wire({
    "/settings": () => json({ ...SETTINGS, agent: { ...SETTINGS.agent, name: REVIEWER.name } }),
    "/connections": () => json({ connections: [] }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Applied." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + SECOND_ID;
  render(<App agents={[AGENT, REVIEWER]} member={MEMBER} onAgents={() => {}} />);
  const dialog = within(await openAgentSettings("Code Reviewer"));

  expect(dialog.getByRole("heading", { name: "Code Reviewer" })).toBeTruthy();

  await userEvent.click(dialog.getByRole("button", { name: "Save prompt" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0].name).toBe("code reviewer");
});

/** The setup act moves the page like every other act, so the router writes it: the member lands on
 *  the app's new conversation, the dialog the act was pressed in goes with the screen under it, and
 *  the composer that stands there is the one they read. The act founds nothing — a grant binds to the
 *  speaker in the conversation it is made in, so the words are the member's to send. */
test("Start setup lands the member on the app's new conversation, founding nothing", async () => {
  const { calls } = wire({
    "/settings": () => json(NEEDS_SETUP),
    "/connections": () => json({ connections: [] }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  const dialog = within(await openAgentSettings());

  await userEvent.click(await dialog.findByRole("button", { name: "Start setup" }));

  expect(location.hash).toBe("#/new/" + AGENT_ID);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(await screen.findByLabelText("Message the app")).toBeTruthy();
  expect(calls.filter((url) => url.includes("/chat?conversation="))).toEqual([]);
  expect(StreamFake.opened).toEqual([]);
});

/** Starting a conversation with the open app is an act of its own band, standing with the acts at
 *  the far end: past the name the band leads with, and before the settings act that ends the row.
 *  It founds the conversation where the pane stands, with the app the pane shows, so the screen is
 *  not left for the chat. */
test("the app pane's header starts a chat with the app it shows, standing with the acts at the far end", async () => {
  const sent: string[] = [];
  location.hash = "#/agents/" + SECOND_ID;
  wire({
    "/api/agents": () => boot([AGENT, RESEARCH], ADMIN),
    "/chat": (url) => {
      sent.push(url);
      return json(OPENED);
    },
    "/transcript": () => json({ messages: [] }),
  });
  render(<Portal />);

  const pane = within(await screen.findByRole("region", { name: "Research" }));
  const act = pane.getByRole("button", { name: "New" });
  const named = pane.getByRole("heading", { level: 2, name: FRESH });
  const settings = pane.getByRole("button", { name: "Settings for Research" });
  expect(named.compareDocumentPosition(act) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(settings.compareDocumentPosition(act) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();

  await userEvent.click(act);

  expect(location.hash).toBe("#/agents/" + SECOND_ID + "?open=new");
  await userEvent.type(await pane.findByLabelText("Message the app"), "hello");
  await userEvent.click(pane.getByRole("button", { name: "Send" }));

  await waitFor(() => expect(sent.length).toBe(1));
  expect(sent[0]).toBe("/surface/web/agents/" + SECOND_ID + "/chat?conversation=new");
});

test("the wizard's bare address founds nothing and forwards to the apps screen", async () => {
  const sent: string[] = [];
  wire({
    "/api/agents": () => boot([AGENT], MEMBER),
    "/chat": (url) => {
      sent.push(url);
      return json(OPENED);
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/builder";
  render(<Portal />);

  await screen.findByRole("region", { name: agentName(AGENT.name) });
  await waitFor(() => expect(location.hash).toBe("#/agents"));
  expect(screen.queryByRole("region", { name: "App Builder" })).toBeNull();
  expect(StreamFake.opened.length).toBe(0);
  expect(sent).toEqual([]);
});
