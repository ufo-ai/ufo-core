import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { Portal } from "@/Portal";
import { AGENT_ICONS } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";
import {
  RESTING_STATUS_MS,
  WORKING_STATUS_MS,
  type AgentStatus,
} from "@/lib/appStatusStore";
import { chatState } from "@/lib/chatStore";
import { sendMessage } from "@/lib/turnStream";

import {
  AGENT,
  AGENT_ID,
  CHAT_ROW,
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
  chatsOnWire,
  json,
  objectIndex,
  openAgentRow,
  openAgentSettings,
  openNewApplication,
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

/** The drawer holding the workspace column, opened. Every act that moves the page shuts it, so a
 *  test that navigates and then reads the column again opens it again; one already standing is left
 *  as it is. */
async function openDrawer(): Promise<void> {
  if (document.querySelector("[data-slot=nav-drawer]")) return;
  await userEvent.click(await screen.findByRole("button", { name: "Menu" }));
  await screen.findByRole("dialog");
}

/** The apps index, which is a section of that column: its rows, the work printed under them, the pin
 *  act beside them, and the run a build is standing in. The drawer draws the column whole. */
async function shownIndex(): Promise<HTMLElement> {
  await openDrawer();
  return agentIndex();
}

/** The drawer stands over the page as a modal, so the page under it is out of reach until it is
 *  shut — which is what a test that reads the column and then the screen does next. */
async function shutDrawer(): Promise<void> {
  if (!document.querySelector("[data-slot=nav-drawer]")) return;
  await userEvent.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
}

/** The act that builds an app is the apps section's own first row, so the wizard is opened out of
 *  the same column. */
async function openWizard() {
  await openDrawer();
  await openNewApplication();
  return screen.findByRole("region", { name: "App Builder" });
}

function progress(): HTMLElement {
  return screen.getByRole("progressbar", { name: "App Builder" });
}

/** The workspace column — the apps index, the act that builds one, the run a build stands in — is
 *  drawn at a phone width alone, where the drawer the hamburger opens holds it. This suite is about
 *  what stands in that column, so it reads the portal at the width that draws it. */
beforeEach(() => {
  location.hash = "";
  atPhoneWidth();
  useStreamFake();
});

test("apps the workspace ships on its first turn reach the sidebar with no reload", async () => {
  /** A workspace provisions the apps it ships on its first turn — a turn the member takes from
   *  inside a portal that has already booted. The read that seeded the sidebar therefore predates
   *  every one of them, and a column that waited for a reload would state one app through the whole
   *  of a member's first session. */
  let shipped = [AGENT];
  wire({
    "/api/agents": () => boot(shipped, ADMIN),
    "/api/agents/status": () =>
      json({ statuses: shipped.map((agent) => status(agent.id, {})) }),
    "/transcript": () => json({ messages: [] }),
  });
  vi.useFakeTimers();
  try {
    render(<Portal />);
    const settle = async (ms: number) => {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(ms);
        await Promise.resolve();
        await Promise.resolve();
      });
    };
    await settle(0);
    // The column stands in the drawer, which is opened once here and left standing: the rows under
    // test change while the member is reading them.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Menu" }));
    });
    await settle(0);
    const index = screen.getByRole("navigation", { name: "Apps" });
    const row = (name: string) =>
      within(index).queryByRole("button", { name: new RegExp("^" + name) });
    expect(row("Assistant")).toBeTruthy();
    expect(row("Research")).toBeNull();

    // The turn lands and the workspace gains its apps. The status read answers for the new agent
    // before any re-read of the boot payload, which is the signal the sidebar has gained one.
    shipped = [AGENT, RESEARCH];

    await settle(RESTING_STATUS_MS);
    expect(row("Research")).toBeTruthy();
  } finally {
    vi.useRealTimers();
  }
});

test("New application opens the wizard speaking in the pane, and the apps list keeps its rows", async () => {
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
  expect(within(wizard).getByLabelText("Ask UFO")).toBeTruthy();
  expect(within(await shownIndex()).getByText("Assistant")).toBeTruthy();

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
  expect(calls.some((url) => url.includes("/agents/" + SECOND_ID + "/homepage"))).toBe(false);
  expect(calls.some((url) => url.includes("/agents/" + SECOND_ID + "/conversations"))).toBe(false);
});

test("a shipped compose screen skips homepage and conversation reads", async () => {
  const SITE = { state: "set", url: "https://ingress.test/chat", deploy_generation: 3 };
  const CHAT = {
    ...AGENT,
    id: SECOND_ID,
    name: "chat",
    main: false,
    app: "chat",
    icon: "aten",
    homepage: SITE,
  };
  const { calls } = wire({
    "/api/agents": () => boot([AGENT, CHAT], ADMIN),
  });
  location.hash = "#/agents/" + SECOND_ID + "?open=compose";
  render(<Portal />);

  const region = await screen.findByRole("region", { name: /chat homepage/i });
  expect(region.querySelector("iframe")?.getAttribute("src")).toBe("https://ingress.test/chat");
  expect(calls.some((url) => url.includes("/agents/" + SECOND_ID + "/homepage"))).toBe(false);
  expect(calls.some((url) => url.includes("/agents/" + SECOND_ID + "/conversations"))).toBe(false);
});

test("a compose screen without a shipped page opens the portal composer without an index", async () => {
  const CHAT = {
    ...AGENT,
    id: SECOND_ID,
    name: "chat",
    main: false,
    app: "chat",
    icon: "aten",
    homepage: { state: "none" },
  };
  const { calls } = wire({
    "/api/agents": () => boot([AGENT, CHAT], ADMIN),
  });
  location.hash = "#/agents/" + SECOND_ID + "?open=compose";
  render(<Portal />);

  expect(await screen.findByLabelText("Ask UFO")).toBeTruthy();
  expect(calls.some((url) => url.includes("/agents/" + SECOND_ID + "/homepage"))).toBe(false);
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

  await userEvent.type(within(wizard).getByLabelText("Ask UFO"), "A finances dashboard.");
  await userEvent.click(within(wizard).getByRole("button", { name: "Send" }));

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  expect(within(wizard).getByText(saying("A finances dashboard."))).toBeTruthy();
  // The bar reads the founded conversation's own board, and the rail row takes its title — neither
  // is reachable from a wizard that lost the conversation.
  expect(await waitFor(progress)).toBeTruthy();
  expect(within(await shownIndex()).getByText("App Builder: " + TITLE)).toBeTruthy();
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

  const index = await shownIndex();
  expect(within(index).queryByText("App Builder")).toBeNull();

  await openNewApplication();

  // Until admission answers there is no conversation and nothing to call it, so the row states the
  // run alone; the title the chat route hands back names it from then on.
  const running = await shownIndex();
  expect(await within(running).findByText("App Builder")).toBeTruthy();
  expect(within(running).queryByRole("button", { name: /App Builder/ })).toBeNull();

  answer();

  expect(await within(await shownIndex()).findByText("App Builder: " + TITLE)).toBeTruthy();
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
  expect(await within(await shownIndex()).findByText("App Builder: " + TITLE)).toBeTruthy();
  await shutDrawer();
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
  await shownIndex();
  await openAgentRow("Research");

  expect(location.hash).toBe("#/agents/" + SECOND_ID);
  expect(screen.queryByText("No such app.")).toBeNull();
  expect(await screen.findByRole("region", { name: "Research" })).toBeTruthy();
});

/** The rows are the section's own list, and the act that adds to it is the section head's: the
 *  index states apps, and what to do about apps is behind the name it stands under. */
test("the acts stand above the places, and the apps list holds only apps", async () => {
  location.hash = "#/agents";
  wire({ "/api/agents": () => boot([AGENT, RESEARCH], ADMIN) });
  render(<Portal />);

  const index = await shownIndex();

  // The band folds the section and offers nothing else, so it holds no menu to hide an act in.
  const band = screen.getByRole("button", { name: "Apps" });
  expect(band.getAttribute("aria-haspopup")).toBeNull();
  expect(screen.queryByRole("button", { name: "Apps options" })).toBeNull();

  // Starting a conversation and building an app are things a member does rather than screens they
  // go to, so both stand at the head of the nav and neither is a row of the list.
  const sidebar = screen.getByRole("navigation", { name: "Workspace" });
  const names = within(sidebar)
    .getAllByRole("button")
    .map((row) => row.getAttribute("aria-label") ?? row.textContent);
  expect(names.indexOf("New chat")).toBeGreaterThan(-1);
  expect(names.indexOf("Create app")).toBe(names.indexOf("New chat") + 1);
  expect(names.indexOf("Create app")).toBeLessThan(names.indexOf("Apps"));

  const rows = within(index)
    .getAllByRole("button")
    .map((row) => row.getAttribute("aria-label") ?? row.textContent);
  expect(rows).not.toContain("Create app");
  expect(rows).not.toContain("New app");
});

test("pressing a section's band folds it away, and the fold holds across a reload", async () => {
  location.hash = "#/agents";
  wire({ "/api/agents": () => boot([AGENT, RESEARCH], ADMIN) });
  const first = render(<Portal />);

  const index = await shownIndex();
  expect(within(index).queryByRole("button", { name: /^Research/ })).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Apps" }));
  await waitFor(() =>
    expect(screen.queryByRole("navigation", { name: "Apps" })).toBeNull(),
  );
  expect(screen.getByRole("button", { name: "Apps" }).getAttribute("aria-expanded")).toBe("false");

  // A section shut to the part someone works from would open again on every reload otherwise.
  first.unmount();
  render(<Portal />);
  await openDrawer();
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Apps" }).getAttribute("aria-expanded")).toBe("false"),
  );
  expect(screen.queryByRole("navigation", { name: "Apps" })).toBeNull();

  // And it opens again from the same band.
  await userEvent.click(screen.getByRole("button", { name: "Apps" }));
  expect(await screen.findByRole("navigation", { name: "Apps" })).toBeTruthy();
});

/** A phone screen has no width for a column beside the page, so the nav drawer holds the second app
 *  index there until the member picks an app from it. */
test("the drawer holds the apps index at a phone width, and a pick shuts it", async () => {
  atPhoneWidth();
  location.hash = "#/agents";
  wire({ "/api/agents": () => boot([AGENT, RESEARCH], ADMIN) });
  render(<Portal />);

  expect(await screen.findByRole("heading", { name: "Apps" })).toBeTruthy();
  expect(screen.queryByRole("navigation", { name: "Apps" })).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Menu" }));
  const drawer = await screen.findByRole("dialog");
  const index = await shownIndex();
  expect(drawer.contains(index)).toBe(true);
  await userEvent.click(within(index).getByRole("button", { name: /^Research/ }));

  expect(location.hash).toBe("#/agents/" + SECOND_ID);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(await screen.findByRole("region", { name: "Research" })).toBeTruthy();
});

test("closing the wizard gives the pane back to the apps list and clears the run's row", async () => {
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
  expect(within(await shownIndex()).queryByText("App Builder")).toBeNull();
  await shutDrawer();
  expect(await screen.findByRole("heading", { name: "Apps" })).toBeTruthy();
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
  expect(within(await shownIndex()).queryByText(/App Builder/)).toBeNull();
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
  expect(within(await shownIndex()).getByText("App Builder: " + TITLE)).toBeTruthy();
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

  await openDrawer();
  await userEvent.click(screen.getByRole("button", { name: "New chat" }));
  await waitFor(() => expect(screen.queryByRole("region", { name: "App Builder" })).toBeNull());
  const index = await shownIndex();
  await userEvent.click(within(index).getByRole("button", { name: /App Builder/ }));

  const wizard = await screen.findByRole("region", { name: "App Builder" });
  expect(await within(wizard).findByText(saying(OPENING))).toBeTruthy();
  expect(await within(await shownIndex()).findByText("App Builder: " + TITLE)).toBeTruthy();
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

  await shownIndex();
  await openAgentRow("Research");
  await waitFor(() => expect(screen.queryByRole("region", { name: "App Builder" })).toBeNull());
  const index = await shownIndex();
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

  await userEvent.type(await screen.findByLabelText("Ask UFO"), "About our numbers.");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(sent).toEqual(["About our numbers."]));

  const wizard = await openWizard();
  await waitFor(() => expect(sent).toEqual(["About our numbers.", OPENING]));
  expect(within(wizard).getByText(OPENING)).toBeTruthy();

  answerChat();
  await waitFor(() => expect(within(wizard).queryByText("About our numbers.")).toBeNull());
  expect(within(await shownIndex()).getByText("App Builder: " + TITLE)).toBeTruthy();
});

/** A founding send the key turns away is refused to its caller and worn by the key as a fault —
 *  never a silent drop the member reads as words that went somewhere. */
test("a second founding send on a busy key is refused and the key wears the fault", async () => {
  wire({
    "/chat": () => new Promise<Response>(() => {}),
  });
  const target = { key: "new:" + AGENT_ID, agentId: AGENT_ID, agentModel: "opus", conversationId: null };
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

  const index = within(await shownIndex());

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

  await userEvent.click(await screen.findByRole("button", { name: "Edit avatar" }));
  const marks = await screen.findAllByRole("radio");
  expect(marks.length).toBe(Object.keys(AGENT_ICONS).length + 1);
  expect(marks[0].getAttribute("value")).toBe("constructor");
  expect((marks[0] as HTMLInputElement).checked).toBe(true);
});

test("a settings action opens the shared right sheet above the dialog", async () => {
  wire({
    "/settings": () => json(SETTINGS),
    "/connections": () => json({ connections: [] }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  const dialog = await openAgentSettings("Assistant", "Connectors");

  await userEvent.click(within(dialog).getByRole("button", { name: "Add connector" }));

  const form = await screen.findByRole("dialog", { name: "Add connector" });
  expect(dialog.contains(form)).toBe(false);
  expect(form.getAttribute("data-slot")).toBe("sheet-content");
  expect(form.className).toContain("fixed");

  await userEvent.click(within(form).getByRole("button", { name: "Close" }));
  expect(await screen.findByRole("dialog", { name: "Assistant" })).toBe(dialog);
});

test("the selected app archives from its Settings screen", async () => {
  const posted: unknown[] = [];
  const onAgents = vi.fn();
  wire({
    "/settings": () =>
      json({
        ...SETTINGS,
        agent: { ...SETTINGS.agent, name: RESEARCH.name, main: false, archivable: true },
      }),
    "/connections": () => json({ connections: [] }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Applied." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + SECOND_ID;
  render(<App agents={[AGENT, RESEARCH]} member={ADMIN} onAgents={onAgents} />);
  const dialog = await openAgentSettings("Research");

  await userEvent.click(within(dialog).getByRole("button", { name: "Archive" }));
  await userEvent.click(within(dialog).getByRole("button", { name: "Confirm archive" }));

  await waitFor(() => expect(onAgents).toHaveBeenCalledOnce());
  expect(posted).toEqual([{ verb: "delete", kind: "agent", name: "research" }]);
  expect(location.hash).toBe("#/agents");
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("the main app has no Archive action in Settings", async () => {
  wire({
    "/settings": () =>
      json({ ...SETTINGS, agent: { ...SETTINGS.agent, archivable: false } }),
    "/connections": () => json({ connections: [] }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);
  const dialog = await openAgentSettings();

  expect(within(dialog).queryByRole("button", { name: "Archive" })).toBeNull();
});

test("the workspace Apps tab restores an archived app the sidebar does not list", async () => {
  const posted: unknown[] = [];
  const onAgents = vi.fn();
  const restore = {
    name: "restore_application",
    description: "Bring an archived app back under a live name.",
    input_schema: {
      properties: { new_name: { type: "string", title: "New Name", maxLength: 64 } },
      required: ["new_name"],
    },
    call: {
      kind: "agent",
      action: "restore_application",
      name: `~archived-${SECOND_ID}`,
      input: {},
    },
    label: "Restore",
  };
  wire({
    "/actions/agent/": (url, init) => {
      if (init?.method !== "POST") return json({ actions: [restore] });
      posted.push({ url, body: JSON.parse(String(init.body)) });
      return json({ applied: true, message: "Applied." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents?chip=Archived";
  render(
    <App
      agents={[AGENT]}
      archived={[
        {
          id: SECOND_ID,
          name: "invoice-intake",
          object: `~archived-${SECOND_ID}`,
          icon: "aten",
          archived_at: "2026-08-20T12:00:00Z",
        },
      ]}
      member={ADMIN}
      onAgents={onAgents}
    />,
  );
  const index = within(await shownIndex());
  expect(index.queryByText("Invoice Intake")).toBeNull();
  await shutDrawer();
  expect(await screen.findByRole("heading", { name: "Apps" })).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Restore" }));
  const dialog = await screen.findByRole("dialog");
  const name = await within(dialog).findByRole("textbox", { name: "New Name" });
  expect((name as HTMLInputElement).value).toBe("invoice-intake");
  await userEvent.clear(name);
  await userEvent.type(name, "invoice-intake-2");
  await userEvent.click(within(dialog).getByRole("button", { name: "Restore" }));

  await waitFor(() => expect(onAgents).toHaveBeenCalledOnce());
  expect(posted).toEqual([
    {
      url: `/surface/web/agents/${AGENT_ID}/actions/agent/~archived-${SECOND_ID}/restore_application`,
      body: { new_name: "invoice-intake-2" },
    },
  ]);
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("each workspace app opens its own settings", async () => {
  const { calls } = wire({
    "/settings": () =>
      json({
        ...SETTINGS,
        agent: { ...SETTINGS.agent, name: RESEARCH.name, main: false, archivable: true },
      }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents";
  render(<App agents={[AGENT, RESEARCH]} archived={[]} member={ADMIN} onAgents={() => {}} />);

  expect(await screen.findByRole("heading", { name: "Apps" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Settings for Assistant" })).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Settings for Research" }));

  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getAllByText("Research")).toHaveLength(2);
  expect(await within(dialog).findByText("Profile")).toBeTruthy();
  expect(calls).toContain(`/surface/web/agents/${SECOND_ID}/settings`);
});

test("the workspace Apps tab lists every app and narrows to the member's own", async () => {
  wire({ "/transcript": () => json({ messages: [] }) });
  location.hash = "#/agents";
  render(
    <App
      agents={[AGENT, { ...RESEARCH, mine: true }]}
      archived={[]}
      member={ADMIN}
      onAgents={() => {}}
    />,
  );
  expect(await screen.findByRole("heading", { name: "Apps" })).toBeTruthy();
  const all = within(await screen.findByRole("table"));
  expect(all.getByText("Assistant")).toBeTruthy();
  expect(all.getByText("Research")).toBeTruthy();

  await userEvent.click(screen.getByRole("tab", { name: "Created by me" }));
  const own = within(await screen.findByRole("table"));
  await waitFor(() => expect(own.queryByText("Assistant")).toBeNull());
  expect(own.getByText("Research")).toBeTruthy();
  expect(location.hash).toContain("chip=Created");

  await userEvent.click(screen.getByRole("tab", { name: "Archived" }));
  expect(await screen.findByText("No apps are archived.")).toBeTruthy();
});

test("scheduled task sheets close back to their settings list", async () => {
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

  await openRow("daily-brief");
  const daily = await screen.findByRole("dialog", { name: "daily-brief" });
  expect(daily.getAttribute("data-slot")).toBe("sheet-content");
  await userEvent.click(within(daily).getByRole("button", { name: "Close" }));

  await openRow("weekly-roll");
  const weekly = await screen.findByRole("dialog", { name: "weekly-roll" });
  expect(weekly.getAttribute("data-slot")).toBe("sheet-content");
  await userEvent.click(within(weekly).getByRole("button", { name: "Close" }));

  await userEvent.click(within(dialog).getByRole("button", { name: "Scheduled" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "Settings" }));
  await userEvent.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

  const reopened = await openAgentSettings("Assistant", "Scheduled");
  expect(await within(reopened).findByText("daily-brief")).toBeTruthy();
  expect(document.querySelectorAll("[data-slot=sheet-content]")).toHaveLength(0);
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

  await userEvent.click(await screen.findByRole("button", { name: "Edit avatar" }));
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

  await userEvent.click(await screen.findByRole("button", { name: "Edit avatar" }));
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
  await userEvent.click(screen.getByRole("button", { name: "Save avatar" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toEqual({
    verb: "apply",
    kind: "agent",
    name: "assistant",
    spec: { icon: "krepis" },
  });
  expect(await screen.findByText("Assistant saved.")).toBeTruthy();
  // Saving closes the picker, so the mark it kept is read by opening it again.
  await userEvent.click(await screen.findByRole("button", { name: "Edit avatar" }));
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

function status(agentId: string, held: Partial<AgentStatus>): AgentStatus {
  return {
    agent_id: agentId,
    turn: null,
    activity: null,
    last_active_at: null,
    last_failed: false,
    ...held,
  };
}

test("the index stands by name, and a pin moves one row above them", async () => {
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

  const index = await shownIndex();
  const drawn = () =>
    within(index)
      .getAllByRole("button", { name: /^(Assistant|Research|Scribe)/ })
      .map((row) => row.querySelector(".text-label")!.textContent);
  // Research is working and Assistant ran an hour ago, and neither moves a row: the column is the
  // pins and then the names, so a member reaching for a row finds it where they left it.
  await waitFor(() => expect(drawn()).toEqual(["Assistant", "Research", "Scribe"]));

  // A pin takes the top and leaves the names under it in order.
  await userEvent.click(within(index).getByRole("button", { name: "Pin Scribe" }));

  await waitFor(() => expect(drawn()).toEqual(["Scribe", "Assistant", "Research"]));
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

  const index = await shownIndex();
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

test("an app that is not working states nothing under its name, and opens nothing at the pointer", async () => {
  wire({
    "/api/agents/status": () =>
      json({
        statuses: [
          status(SECOND_ID, { last_failed: true }),
          status(AGENT_ID, { last_active_at: HOURS_AGO }),
        ],
      }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents";
  render(<App agents={[AGENT, RESEARCH]} member={MEMBER} onAgents={() => {}} />);

  const index = await shownIndex();
  // A failed run and a run five hours ago are both read off the dot. Neither takes a line, and
  // nothing stands in for one at the pointer.
  const failed = within(index).getByRole("button", { name: /^Research/ });
  await waitFor(() => expect(failed.querySelector(".bg-blocked")).toBeTruthy());
  expect(failed.textContent).toBe("Research");

  const quiet = within(index).getByRole("button", { name: /^Assistant/ });
  expect(quiet.textContent).toBe("Assistant");
  fireEvent.focus(quiet);
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

  const index = await shownIndex();
  const dot = (name: RegExp, tone: string) =>
    within(index).getByRole("button", { name }).querySelector("." + tone);
  await waitFor(() => expect(dot(/^Assistant/, "bg-live")).toBeTruthy());
  expect(dot(/^Research/, "bg-blocked")).toBeTruthy();
  expect(dot(/^Scribe/, "bg-blocked")).toBeTruthy();
  expect(dot(/^Watcher/, "bg-live")).toBeNull();
  expect(dot(/^Watcher/, "bg-blocked")).toBeNull();
});

test("an app installed and not set up wears the blocked dot, and work outranks it", async () => {
  wire({
    "/api/agents/status": () => json({ statuses: [status(AGENT_ID, { turn: "running" })] }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents";
  render(
    <App
      agents={[
        { ...AGENT, setup_due: true },
        { ...RESEARCH, setup_due: true },
        { ...SCRIBE, setup_due: false },
      ]}
      member={MEMBER}
      onAgents={() => {}}
    />,
  );

  const index = await shownIndex();
  const dot = (name: RegExp, tone: string) =>
    within(index).getByRole("button", { name }).querySelector("." + tone);

  // Owed setup is the app waiting on the member, which is what the blocked tone says.
  await waitFor(() => expect(dot(/^Research/, "bg-blocked")).toBeTruthy());
  // An app that owes setup and is working states the work: something is happening now either way.
  expect(dot(/^Assistant/, "bg-live")).toBeTruthy();
  expect(dot(/^Assistant/, "bg-blocked")).toBeNull();
  // An app that owes nothing and is doing nothing wears no dot at all.
  expect(dot(/^Scribe/, "bg-blocked")).toBeNull();
  expect(dot(/^Scribe/, "bg-live")).toBeNull();
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

  const index = await shownIndex();
  const row = () => within(index).getByRole("button", { name: /^Research/ });
  await waitFor(() => expect(row().querySelector(".bg-live")).toBeTruthy());
  row().focus();
  expect(document.activeElement).toBe(row());

  // The work ends and the row closes its line over nothing: the row is one element across that
  // change, so whoever was standing on it still is.
  working = false;
  await waitFor(() => expect(row().querySelector(".bg-live")).toBeNull(), {
    timeout: 2 * WORKING_STATUS_MS,
  });
  expect(document.activeElement).toBe(row());
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
  // Off the apps pane, so the sidebar's index is the only reader of the status route — the poll
  // under test is its own, not one the pane also runs.
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
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Menu" }));
    });
    await settle(0);
    const index = screen.getByRole("navigation", { name: "Apps" });
    const row = () => within(index).getByRole("button", { name: /^Research/ });
    expect(row().querySelector(".bg-live")).toBeTruthy();

    await settle(WORKING_STATUS_MS);
    expect(answers).toBe(2);
    // The refusal does not replace the answer before it, so the row keeps the dot it was drawn
    // with rather than stating the app had gone quiet.
    expect(row().querySelector(".bg-live")).toBeTruthy();

    await settle(WORKING_STATUS_MS);
    expect(answers).toBe(3);
    expect(row().querySelector(".bg-live")).toBeTruthy();
  } finally {
    vi.useRealTimers();
  }
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

  const index = within(await shownIndex());
  expect(index.getByRole("button", { name: /^Code Reviewer/ })).toBeTruthy();
  expect(index.queryByText("code reviewer")).toBeNull();
  await shutDrawer();

  const pane = within(await screen.findByRole("region", { name: "Code Reviewer" }));
  expect(pane.getByRole("button", { name: "Menu for Code Reviewer" })).toBeTruthy();
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

  expect(dialog.getByRole("navigation", { name: "Breadcrumb" }).textContent).toBe(
    "Code Reviewer/Settings",
  );

  await userEvent.click(dialog.getByRole("button", { name: "Edit prompt" }));
  await userEvent.click(dialog.getByRole("button", { name: "Save prompt" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0].name).toBe("code reviewer");
});

const PURPOSE = "Briefs every meeting before it starts.";

test("an app the workspace has never built stands on its setup screen", async () => {
  /** Built is the line, not wired: an app builds a thinner page from fewer sources, and a member
   *  who wants to see it before every todo is settled gets to. */
  wire({
    "/settings": () => json({ ...NEEDS_SETUP, agent: { ...NEEDS_SETUP.agent, main: false } }),
    "/connections": () => json({ connections: [] }),
    "/transcript": () => json({ messages: [] }),
    "/homepage": () =>
      json({ state: "set", url: "https://meetings.example.test", deploy_generation: 1 }),
    "/setup": () =>
      json({
        credentials: [],
        connectors: [{ provider: "googlecalendar", granted: false }],
        standing: [],
        own_page: false,
      }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(
    <App
      agents={[
        { ...AGENT, main: false, app: "meetings", purpose: PURPOSE, stands_on_setup: true },
      ]}
      member={MEMBER}
      onAgents={() => {}}
    />,
  );

  await waitFor(() => expect(location.hash).toBe("#/agents/" + AGENT_ID + "/setup"));
  // The screen names the app it is setting up and states the app's one sentence under it — the
  // member arrived at an app somebody else installed, and reads what it is for before the list of
  // what it is waiting on.
  await screen.findByText(/Set up your .* app/);
  await screen.findByText(PURPOSE);
});

/** The setup screen draws no navigation, and neither does the app's own address while the app
 *  stands on that screen: the column is not drawn for one address and taken away by the move to the
 *  other, which is a sidebar the member watches appear and vanish. */
test("an app in setup draws no sidebar, on its own address or on the setup one", async () => {
  wire({
    ...chatsOnWire([CHAT_ROW]),
    "/settings": () => json({ ...NEEDS_SETUP, agent: { ...NEEDS_SETUP.agent, main: false } }),
    "/connections": () => json({ connections: [] }),
    "/transcript": () => json({ messages: [] }),
    "/homepage": () =>
      json({ state: "set", url: "https://meetings.example.test", deploy_generation: 1 }),
    "/setup": () =>
      json({
        credentials: [],
        connectors: [{ provider: "googlecalendar", granted: false }],
        standing: [],
        own_page: false,
      }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(
    <App
      agents={[{ ...AGENT, main: false, app: "meetings", stands_on_setup: true }]}
      member={MEMBER}
      onAgents={() => {}}
    />,
  );

  // The first render already draws none of it: the boot read carries the fact, so no frame stands
  // in which the column is on screen. The hamburger is the whole way to it at this width, so its
  // absence is the column's.
  expect(screen.queryByRole("button", { name: "Menu" })).toBeNull();
  expect(screen.queryByRole("navigation", { name: "Workspace" })).toBeNull();
  await waitFor(() => expect(location.hash).toBe("#/agents/" + AGENT_ID + "/setup"));
  await screen.findByText(/Set up your .* app/);
  expect(screen.queryByRole("button", { name: "Menu" })).toBeNull();
  expect(screen.queryByRole("navigation", { name: "Workspace" })).toBeNull();
});

/** An app that has been built reaches the same address of its own accord — that screen is where an
 *  account is reconnected — and there the member is standing in a workspace they move around from. */
test("a built app's setup screen keeps the sidebar", async () => {
  wire({
    ...chatsOnWire([CHAT_ROW]),
    "/setup": () =>
      json({
        credentials: [],
        connectors: [{ provider: "googlecalendar", granted: false }],
        standing: [],
        own_page: true,
      }),
  });
  location.hash = "#/agents/" + AGENT_ID + "/setup";
  render(
    <App
      agents={[{ ...AGENT, main: false, app: "meetings" }]}
      member={MEMBER}
      onAgents={() => {}}
    />,
  );

  await screen.findByText(/Set up your .* app/);
  expect(await shownIndex()).toBeTruthy();
});

test("a shipped app that declares no setup stands on its page", async () => {
  /** Chat, radar, tasks, wiki and artifacts declare none, and their page is the deploy's own.
   *  A gate that read "no forked page" as "not set up" stranded them on a screen with no account
   *  row, no schedule row, and nothing to press. */
  wire({
    "/settings": () => json({ ...NEEDS_SETUP, agent: { ...NEEDS_SETUP.agent, main: false } }),
    "/connections": () => json({ connections: [] }),
    "/transcript": () => json({ messages: [] }),
    "/homepage": () =>
      json({ state: "set", url: "https://wiki.example.test", deploy_generation: 1 }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(
    <App agents={[{ ...AGENT, main: false, app: "wiki" }]} member={MEMBER} onAgents={() => {}} />,
  );

  expect(await screen.findByLabelText(/^Menu for/)).toBeTruthy();
  expect(location.hash).toBe("#/agents/" + AGENT_ID);
});

test("the move to setup replaces, so Back steps past the app", async () => {
  /** A pushed entry sends Back to the app address, which mounts the pane, reads the same answer and
   *  pushes the setup screen over it again — the member could never step back past the app. */
  const replaced: unknown[] = [];
  const real = history.replaceState.bind(history);
  vi.spyOn(history, "replaceState").mockImplementation((...args) => {
    replaced.push(args[2]);
    return real(...(args as Parameters<typeof history.replaceState>));
  });
  wire({
    "/settings": () => json({ ...NEEDS_SETUP, agent: { ...NEEDS_SETUP.agent, main: false } }),
    "/connections": () => json({ connections: [] }),
    "/transcript": () => json({ messages: [] }),
    "/homepage": () =>
      json({ state: "set", url: "https://meetings.example.test", deploy_generation: 1 }),
    "/setup": () =>
      json({
        connectors: [{ provider: "googlecalendar", granted: false }],
        credentials: [],
        standing: [],
        own_page: false,
      }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(
    <App
      agents={[{ ...AGENT, main: false, app: "meetings", stands_on_setup: true }]}
      member={MEMBER}
      onAgents={() => {}}
    />,
  );

  await waitFor(() => expect(location.hash).toBe("#/agents/" + AGENT_ID + "/setup"));
  expect(replaced).toContain("#/agents/" + AGENT_ID + "/setup");
});

/** The roster is what the page was told at boot, and a build binds the app's page long after that:
 *  the app the member has just built still wears the flag for the rest of the session. The app's own
 *  read is what says the page is there, so the member opens it, and the roster is read again — which
 *  is what puts the sidebar back. */
test("an app built since the boot read opens at its own address", async () => {
  const roster = vi.fn();
  wire({
    ...chatsOnWire([CHAT_ROW]),
    "/settings": () => json({ ...NEEDS_SETUP, agent: { ...NEEDS_SETUP.agent, main: false } }),
    "/connections": () => json({ connections: [] }),
    "/transcript": () => json({ messages: [] }),
    "/homepage": () =>
      json({ state: "set", url: "https://meetings.example.test", deploy_generation: 1 }),
    "/setup": () =>
      json({
        credentials: [],
        connectors: [{ provider: "googlecalendar", granted: false }],
        standing: [],
        own_page: true,
      }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(
    <App
      agents={[{ ...AGENT, main: false, app: "meetings", stands_on_setup: true }]}
      member={MEMBER}
      onAgents={roster}
    />,
  );

  expect(await screen.findByLabelText(/^Menu for/)).toBeTruthy();
  await waitFor(() => expect(roster).toHaveBeenCalled());
  expect(location.hash).toBe("#/agents/" + AGENT_ID);
});

/** A read that failed says nothing about where the app stands. Moving on it would put the member on
 *  a setup screen whose own read failed too, and the crumb back to the app would move them there
 *  again. */
test("a setup read that failed leaves the member on the app they opened", async () => {
  const { calls } = wire({
    ...chatsOnWire([CHAT_ROW]),
    "/settings": () => json({ ...NEEDS_SETUP, agent: { ...NEEDS_SETUP.agent, main: false } }),
    "/connections": () => json({ connections: [] }),
    "/transcript": () => json({ messages: [] }),
    "/homepage": () =>
      json({ state: "set", url: "https://meetings.example.test", deploy_generation: 1 }),
    "/setup": () => new Response("nope", { status: 503 }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(
    <App
      agents={[{ ...AGENT, main: false, app: "meetings", stands_on_setup: true }]}
      member={MEMBER}
      onAgents={() => {}}
    />,
  );

  expect(await screen.findByLabelText(/^Menu for/)).toBeTruthy();
  await waitFor(() => expect(calls.some((url) => url.includes("/setup"))).toBe(true));
  expect(location.hash).toBe("#/agents/" + AGENT_ID);
});

test("an agent no extension shipped keeps its conversation column, however unbuilt", async () => {
  /** The main agent and an agent a member built have no bound site and are never meant to have
   *  one. A gate that read "no site" as "never built" sent them to a setup screen over an empty
   *  declaration, and their conversation column could not be reached at its own address. */
  const { calls } = wire({
    "/settings": () => json(SETTINGS),
    "/connections": () => json({ connections: [] }),
    "/transcript": () => json({ messages: [] }),
    "/homepage": () => json({ state: "none" }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByLabelText("Ask UFO")).toBeTruthy();
  expect(location.hash).toBe("#/agents/" + AGENT_ID);
  // The pane asks about an app the roster says the workspace is building, and this is not one.
  expect(calls.filter((url) => url.includes("/setup"))).toEqual([]);
});

test("an app that has been built once stands on its page, and the setup screen is gone", async () => {
  wire({
    "/settings": () => json({ ...NEEDS_SETUP, agent: { ...NEEDS_SETUP.agent, main: false } }),
    "/connections": () => json({ connections: [] }),
    "/transcript": () => json({ messages: [] }),
    "/homepage": () =>
      json({ state: "set", url: "https://meetings.example.test", deploy_generation: 1 }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(
    <App agents={[{ ...AGENT, main: false, app: "meetings" }]} member={MEMBER} onAgents={() => {}} />,
  );

  expect(await screen.findByLabelText(/^Menu for/)).toBeTruthy();
  expect(location.hash).toBe("#/agents/" + AGENT_ID);
  // And it is somewhere the member moves around from, so the column is theirs to open: the setup
  // screen is the one place it is not.
  expect(await shownIndex()).toBeTruthy();
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
  const menu = pane.getByRole("button", { name: "Menu for Research" });
  expect(named.compareDocumentPosition(act) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(menu.compareDocumentPosition(act) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();

  await userEvent.click(act);

  expect(location.hash).toBe("#/agents/" + SECOND_ID + "?open=new");
  await userEvent.type(await pane.findByLabelText("Ask UFO"), "hello");
  await userEvent.click(pane.getByRole("button", { name: "Send" }));

  await waitFor(() => expect(sent.length).toBe(1));
  expect(sent[0]).toBe("/surface/web/agents/" + SECOND_ID + "/chat?conversation=new");
});

test("the app pane's menu opens each app setting on its matching tab", async () => {
  location.hash = "#/agents/" + SECOND_ID;
  wire({
    "/api/agents": () => boot([AGENT, RESEARCH], ADMIN),
    "/settings": () => json({ ...SETTINGS, agent: { ...SETTINGS.agent, name: RESEARCH.name } }),
    "/connections": () => json({ connections: [] }),
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, []),
    "/transcript": () => json({ messages: [] }),
  });
  render(<Portal />);

  const pane = within(await screen.findByRole("region", { name: "Research" }));
  const trigger = pane.getByRole("button", { name: "Menu for Research" });
  expect(trigger.querySelector("svg")?.classList.contains("tabler-icon-dots")).toBe(true);

  for (const tab of ["Settings", "Connectors", "Scheduled"] as const) {
    await userEvent.click(trigger);
    const menu = within(await screen.findByRole("menu"));
    expect(menu.getAllByRole("menuitem").map((entry) => entry.textContent)).toEqual([
      "Settings",
      "Connectors",
      "Scheduled",
    ]);

    await userEvent.click(menu.getByRole("menuitem", { name: tab }));
    const panel = await screen.findByRole("dialog", { name: "Research" });
    // The band names the read showing, and the read is reached under that name alone.
    expect(within(panel).getByRole("button", { name: tab })).toBeTruthy();
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  }
});

test("the band's menu and the panel's own draw the three reads identically", async () => {
  location.hash = "#/agents/" + SECOND_ID;
  wire({
    "/api/agents": () => boot([AGENT, RESEARCH], ADMIN),
    "/settings": () => json({ ...SETTINGS, agent: { ...SETTINGS.agent, name: RESEARCH.name } }),
    "/connections": () => json({ connections: [] }),
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, []),
    "/transcript": () => json({ messages: [] }),
  });
  render(<Portal />);

  const pane = within(await screen.findByRole("region", { name: "Research" }));
  await userEvent.click(pane.getByRole("button", { name: "Menu for Research" }));
  const fromBand = [...(await screen.findByRole("menu")).querySelectorAll('[role="menuitem"]')].map(
    (item) => item.outerHTML,
  );
  await userEvent.keyboard("{Escape}");

  const panel = within(await openAgentSettings("Research", "Settings"));
  await userEvent.click(panel.getByRole("button", { name: "Settings" }));
  const fromPanel = [...(await screen.findByRole("menu")).querySelectorAll('[role="menuitem"]')].map(
    (item) => item.outerHTML,
  );

  // Same words, same marks, same set — one list stood in two places.
  expect(fromPanel.length).toBe(3);
  expect(fromPanel.map(stripIds)).toEqual(fromBand.map(stripIds));
});

/** Radix stamps each menu item with its own generated ids, which differ between two mounts of the
 *  same list and say nothing about how the item is drawn. */
function stripIds(markup: string): string {
  return markup.replaceAll(/(id|aria-labelledby|aria-describedby|data-radix-[a-z-]*)="[^"]*"/g, "");
}

test("the app's panel switches reads from its own band, without going back to the app menu", async () => {
  location.hash = "#/agents/" + SECOND_ID;
  wire({
    "/api/agents": () => boot([AGENT, RESEARCH], ADMIN),
    "/settings": () => json({ ...SETTINGS, agent: { ...SETTINGS.agent, name: RESEARCH.name } }),
    "/connections": () => json({ connections: [] }),
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, []),
    "/transcript": () => json({ messages: [] }),
  });
  render(<Portal />);

  const panel = within(await openAgentSettings("Research", "Settings"));
  // The panel stands over the screen it was opened from, which the scrim under it is what says.
  expect(document.querySelector("[data-slot=app-settings-scrim]")).toBeTruthy();
  // The band names the app the read belongs to, because the panel covers the band that would.
  expect(panel.getByRole("navigation", { name: "Breadcrumb" }).textContent).toBe(
    "Research/Settings",
  );

  await userEvent.click(panel.getByRole("button", { name: "Settings" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "Scheduled" }));

  expect(panel.getByRole("button", { name: "Scheduled" })).toBeTruthy();
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

  await screen.findByRole("heading", { name: "Apps" });
  await waitFor(() => expect(location.hash).toBe("#/agents"));
  expect(screen.queryByRole("region", { name: "App Builder" })).toBeNull();
  expect(StreamFake.opened.length).toBe(0);
  expect(sent).toEqual([]);
});
