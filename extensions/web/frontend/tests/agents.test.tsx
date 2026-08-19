import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { Portal } from "@/Portal";
import { AGENT_ICONS } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";
import { chatState } from "@/lib/chatStore";
import { sendMessage } from "@/lib/turnStream";

import {
  AGENT,
  AGENT_ID,
  CONVO_ID,
  MEMBER,
  SECOND_ID,
  SETTINGS,
  StreamFake,
  TURN_ID,
  agentIndex,
  json,
  openAgentRow,
  openAgentSettings,
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
  icon: "telescope",
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

async function openWizard() {
  await userEvent.click(await screen.findByRole("button", { name: "Apps" }));
  await userEvent.click(await screen.findByRole("button", { name: "New application" }));
  return screen.findByRole("region", { name: "App Builder" });
}

function progress(): HTMLElement {
  return screen.getByRole("progressbar", { name: "App Builder" });
}

beforeEach(() => {
  location.hash = "";
  useStreamFake();
});

test("New application opens the wizard speaking in the pane, beside the rail the screen keeps", async () => {
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
  expect(within(wizard).getByLabelText("Message the agent")).toBeTruthy();
  expect(within(await agentIndex()).getByText("Assistant")).toBeTruthy();

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

  await userEvent.type(within(wizard).getByLabelText("Message the agent"), "A finances dashboard.");
  await userEvent.click(within(wizard).getByRole("button", { name: "Send" }));

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  expect(within(wizard).getByText(saying("A finances dashboard."))).toBeTruthy();
  // The bar reads the founded conversation's own board, and the rail row takes its title — neither
  // is reachable from a wizard that lost the conversation.
  expect(await waitFor(progress)).toBeTruthy();
  expect(within(await agentIndex()).getByText("App Builder: " + TITLE)).toBeTruthy();
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

  await userEvent.click(await screen.findByRole("button", { name: "Apps" }));
  const index = await agentIndex();
  expect(within(index).queryByText("App Builder")).toBeNull();

  await userEvent.click(within(index).getByRole("button", { name: "New application" }));

  // Until admission answers there is no conversation and nothing to call it, so the row states the
  // run alone; the title the chat route hands back names it from then on.
  expect(await within(index).findByText("App Builder")).toBeTruthy();
  expect(within(index).queryByRole("button", { name: /App Builder/ })).toBeNull();

  answer();

  expect(await within(index).findByText("App Builder: " + TITLE)).toBeTruthy();
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
  expect(await within(await agentIndex()).findByText("App Builder: " + TITLE)).toBeTruthy();
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
  await openAgentRow("Research");

  expect(location.hash).toBe("#/agents/" + SECOND_ID);
  expect(screen.queryByText("No such agent.")).toBeNull();
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
  expect(within(await agentIndex()).queryByText("App Builder")).toBeNull();
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
  expect(within(await agentIndex()).queryByText(/App Builder/)).toBeNull();
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
  expect(within(await agentIndex()).getByText("App Builder: " + TITLE)).toBeTruthy();
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

  await userEvent.click(screen.getByRole("button", { name: "Chat" }));
  await waitFor(() => expect(screen.queryByRole("region", { name: "App Builder" })).toBeNull());
  await userEvent.click(screen.getByRole("button", { name: "Apps" }));

  const wizard = await screen.findByRole("region", { name: "App Builder" });
  expect(await within(wizard).findByText(saying(OPENING))).toBeTruthy();
  expect(within(await agentIndex()).getByText("App Builder: " + TITLE)).toBeTruthy();
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

  await openAgentRow("Research");
  await waitFor(() => expect(screen.queryByRole("region", { name: "App Builder" })).toBeNull());
  const index = await agentIndex();
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

  await userEvent.type(await screen.findByLabelText("Message the agent"), "About our numbers.");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(sent).toEqual(["About our numbers."]));

  const wizard = await openWizard();
  await waitFor(() => expect(sent).toEqual(["About our numbers.", OPENING]));
  expect(within(wizard).getByText(OPENING)).toBeTruthy();

  answerChat();
  await waitFor(() => expect(within(wizard).queryByText("About our numbers.")).toBeNull());
  expect(within(await agentIndex()).getByText("App Builder: " + TITLE)).toBeTruthy();
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

  await userEvent.click(screen.getByRole("button", { name: "Apps" }));
  const index = within(await agentIndex());

  const assistant = index.getByRole("button", { name: /^Assistant/ });
  expect(assistant.querySelector(".tabler-icon-robot")).toBeTruthy();
  expect(assistant.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
  expect(index.getByRole("button", { name: /^Research/ }).querySelector(".tabler-icon-telescope"))
    .toBeTruthy();
});

test("a member picks another mark, and the pick rides one intent and comes back", async () => {
  const posted: unknown[] = [];
  let icon = "robot";
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
  // The set leads with the workspace's own mark, and the picker draws the set in its own order.
  expect(Object.keys(AGENT_ICONS)[0]).toBe("ufo");
  expect(marks[0].getAttribute("value")).toBe("ufo");
  // Every label is the slug's words capitalized, except the product's own name, which is read as
  // it is written.
  expect(marks[0].getAttribute("aria-label")).toBe("ufo");
  expect(screen.getByRole("radio", { name: "Shopping cart" })).toBeTruthy();
  expect((screen.getByRole("radio", { name: "Robot" }) as HTMLInputElement).checked).toBe(true);
  // The schema hides `icon` the way it hides `prompt`, so the generic form draws no control for it.
  expect(screen.queryByRole("combobox", { name: "icon" })).toBeNull();
  expect(screen.queryByRole("textbox", { name: "icon" })).toBeNull();

  await userEvent.click(screen.getByRole("radio", { name: "Chart line" }));
  await userEvent.click(screen.getByRole("button", { name: "Save icon" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toEqual({
    verb: "apply",
    kind: "agent",
    name: "assistant",
    spec: { icon: "chart-line" },
  });
  expect(await screen.findByText("Applied.")).toBeTruthy();
  await waitFor(() =>
    expect((screen.getByRole("radio", { name: "Chart line" }) as HTMLInputElement).checked).toBe(
      true,
    ),
  );
  expect((screen.getByRole("radio", { name: "Robot" }) as HTMLInputElement).checked).toBe(false);
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

  const index = within(await agentIndex());
  expect(index.getByRole("button", { name: /^Code Reviewer/ })).toBeTruthy();
  expect(index.queryByText("code reviewer")).toBeNull();

  const pane = await screen.findByRole("region", { name: "Code Reviewer" });
  expect(within(pane).getByRole("heading", { name: "Code Reviewer" })).toBeTruthy();
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
