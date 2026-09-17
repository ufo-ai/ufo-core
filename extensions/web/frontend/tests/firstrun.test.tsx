import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, onTestFinished, test, vi } from "vitest";

import { App } from "@/App";
import { WATCH_MS, type FirstRunPayload } from "@/lib/firstRun";
import { BUILD_STEP_MS } from "@/views/FirstRun";
import { resetChatStore } from "@/lib/chatStore";
import {HOME_CONNECTORS_LANE, homeConversationLane} from "@/lib/homeLanes";
import {firstRunHash, homeHash} from "@/lib/route";

import {
  AGENT,
  AGENT_ID,
  CHAT_APP,
  CHAT_APP_ID,
  chatsOnWire,
  CONVO_ID,
  destination,
  json,
  MEMBER,
  type Route,
  TURN_ID,
  useStreamFake,
  wire,
} from "./harness";

const ADMIN = { ...MEMBER, admin: true };

const SLACK_LINK = "https://slack.com/oauth/v2/authorize?state=sealed";

const OPENED = { turn_id: TURN_ID, conversation_id: CONVO_ID, title: "Setting up" };

const WEBSITE_HEADING = "What’s the website for your business?";

const ROLES = [
  "Founder",
  "Designer",
  "Marketing",
  "Operations",
  "Engineer",
  "Growth",
  "Human Resources",
  "Copywriting",
  "Researcher",
  "Executive Assistant",
  "Sales",
  "Other",
];

const SLACK_POINTS = [
  "Mention @ufo or send it a DM.",
  "It replies, remembers, and works with your team.",
  "One install for the whole workspace.",
];

const GOALS = [
  "Growing revenue",
  "Shipping product",
  "Understanding competitors",
  "Finding customers",
  "Hiring",
  "Improving operations",
  "Fundraising",
  "User acquisition",
  "Other",
];

function opens(role: string, goal: string): string {
  return "I just set up this workspace. My business: Design studio. My role: " + role + ". My goal: " + goal + ". ";
}

const REVENUE_ASKS =
  "Start by working out my funnel as it stands from whatever CRM, billing, analytics or " +
  "spreadsheet access I have granted: volume at each stage, conversion between them, and average " +
  "deal size. Name the single stage losing the most, and one experiment on it with a metric. Do " +
  "not guess a number I have not given you — say what you could not read. Ask me at most one " +
  "thing.";

const REVENUE_THREAD = opens("Founder", "growing revenue") + REVENUE_ASKS;

const COMPETITORS_THREAD =
  opens("Founder", "understanding competitors") +
  "Start by naming the competitors my business implies, from my " +
  "website and whatever CRM, support or analytics access I have granted: what each one sells, to " +
  "whom, and at what price. Say where we win and where we lose, in one line each. Do not invent " +
  "a competitor or a price you could not read — say what you could not read. Ask me at most one " +
  "thing.";

const HIRING_THREAD =
  opens("Founder", "hiring") +
  "Start by listing the roles my business is hiring for, from whatever applicant tracking, " +
  "calendar or email access I have granted, and where each one stands: open, interviewing, or " +
  "offered. Name the one role holding the rest back. Contact no candidate and send nothing. Ask " +
  "me at most one thing.";

const OTHER_THREAD =
  opens("Founder", "Launching in Japan") +
  "Start by saying what you can already read about it from whatever access I " +
  "have granted, and the first step you would take. Do not guess what you could not read — say " +
  "so. Ask me at most one thing.";

const OPENING =
  "I just set up this workspace. My business: Design studio. Set up my first task: a daily competitive analysis.";

const DEFAULT_SENT = [OPENING, REVENUE_THREAD];

const HANDED = homeHash({ opens: [homeConversationLane(CONVO_ID), HOME_CONNECTORS_LANE] });

const INSTALL_READS: Record<string, Route> = {
  "/actions/surface/slack$": () =>
    json({
      actions: [
        {
          name: "slack_connect",
          description: "Install the workspace app.",
          input_schema: { properties: {} },
          call: { kind: "surface", action: "slack_connect", name: "slack", input: {} },
          label: "Connect",
        },
      ],
    }),
};

const SURFACES_READ: Record<string, Route> = {
  "/workspace/surfaces$": () =>
    json({
      surfaces: [
        { name: "slack", label: "Slack", offered: true, connected: false, install_command: null },
        { name: "imessage", label: "iMessage", offered: true, connected: false, install_command: null },
        {
          name: "ufo",
          label: "Terminal",
          offered: true,
          connected: false,
          install_command: "curl -fsSL https://ufo.example/ufo | sh",
        },
      ],
    }),
};

const CONFIRM_VIEW: FirstRunPayload["actions"]["enrichment_profile"][number] = {
  name: "confirm_website",
  description: "Confirm the website the enrichment reads.",
  call: { kind: "enrichment_profile", action: "confirm_website", input: {} },
  input_schema: { properties: { website: { type: "string" } }, required: ["website"] },
  label: "Next",
};

const RECORD_VIEW: FirstRunPayload["actions"]["memory"][number] = {
  name: "record_first_run",
  description: "Record what the first run learned.",
  call: { kind: "memory", action: "record_first_run", input: {} },
  input_schema: { properties: { body: { type: "string", maxLength: 400 } }, required: ["body"] },
  label: "Continue",
};

const ACTIONS: Pick<FirstRunPayload, "actions"> = {
  actions: { member: [], memory: [RECORD_VIEW], enrichment_profile: [] },
};

const SLACK_TILE = { name: "slack", label: "Slack", summary: "Send and read messages.", group: "Messaging" };

const DOMAIN = "simplecasual.com";

const FIRST_RUN = {
  providers: [SLACK_TILE, { name: "github", label: "GitHub", summary: "Read and write code.", group: "Code" }],
  mcp_servers: [],
  connectors: [{ name: "slack", label: "Slack", installed: false }],
  model_key_held: true,
  workspace_domain: DOMAIN as string | null,
  ...ACTIONS,
};

const WITH_WEBSITE = {
  ...FIRST_RUN,
  actions: { ...ACTIONS.actions, enrichment_profile: [CONFIRM_VIEW] },
};

const SLACK_ONLY = { ...FIRST_RUN, providers: [SLACK_TILE] };

const RANKED = {
  ...FIRST_RUN,
  providers: [
    SLACK_TILE,
    { name: "github", label: "GitHub", summary: "Read and write code.", group: "Code" },
    { name: "linear", label: "Linear", summary: "Track issues.", group: "Code" },
    { name: "notion", label: "Notion", summary: "Read and write pages.", group: "Docs" },
    { name: "gmail", label: "Gmail", summary: "Read and send mail.", group: "Mail" },
  ],
};

const PROFILE_ROW = {
  name: ADMIN.email,
  summary: "Founder at Simplecasual (Design Consulting)",
  status: "matched",
  full_name: "Alex",
  job_title: "Founder",
  job_title_role: "operations",
  job_title_levels: "owner",
  company_name: "Simplecasual",
  company_industry: "Design Consulting",
  company_size: "1-10",
  company_founded: 2013,
  company_summary: "keep it simple, keep it casual.",
  company_location: null,
};

const LEARNED = "keep it simple, keep it casual.\nDesign Consulting, 1-10 people, founded 2013.";

const PROFILE_READ: Record<string, Route> = {
  "/objects/enrichment_profile": () => json({ objects: [PROFILE_ROW], next_cursor: null }),
};

const NO_SLACK = {
  ...FIRST_RUN,
  ...ACTIONS,
  connectors: [],
};

const HELD_SLACK = {
  ...FIRST_RUN,
  ...ACTIONS,
  connectors: [{ name: "slack", label: "Slack", installed: true }],
};

const WHO = "Design studio. Their website: " + DOMAIN + ". Their role: Founder.";

beforeEach(() => {
  location.hash = "";
  history.replaceState(null, "", location.pathname + "?first=1");
  sessionStorage.clear();
  resetChatStore();
  chat = chatSink();
  acts = recorder();
  useStreamFake();
});

const ANSWERS_KEY = "ufo.first-run." + ADMIN.workspace_id;

function stored(): Record<string, unknown> | null {
  const held = sessionStorage.getItem(ANSWERS_KEY);
  return held ? (JSON.parse(held) as Record<string, unknown>) : null;
}

function mount(
  routes: Record<string, Route> = {},
  member = ADMIN,
  payload = FIRST_RUN,
  agents = [AGENT],
) {
  const wired = wire({
    ...chatsOnWire([]),
    ...INSTALL_READS,
    ...SURFACES_READ,
    "/workspace/first-run": () => json(payload),
    "/actions/": acts.route,
    "/chat": chat.route,
    "/slots": () => json({ slots: [] }),
    "/transcript": () => json({ messages: [] }),
    ...routes,
  });
  render(<App agents={agents} member={member} onAgents={() => {}} />);
  return wired;
}

async function open(
  routes: Record<string, Route> = {},
  member = ADMIN,
  payload = FIRST_RUN,
  agents = [AGENT],
) {
  const wired = mount(routes, member, payload, agents);
  await userEvent.click(await screen.findByRole("button", { name: "Get started" }));
  return wired;
}

function intents(calls: { url: string; body: unknown }[]): unknown[] {
  return calls.map((call) => ({ lane: laneOf(call.url), body: call.body }));
}

function laneOf(url: string): string {
  return url.split("/agents/" + AGENT.id + "/")[1];
}

function recorder(outcomes: Record<string, unknown> = {}): {
  calls: { url: string; body: unknown }[];
  route: Route;
} {
  const calls: { url: string; body: unknown }[] = [];
  return {
    calls,
    route: (url, init) => {
      const body = JSON.parse(String(init?.body));
      calls.push({ url, body });
      const lane = laneOf(url);
      const act = lane === "intents" ? body.verb : lane.split("/").at(-1);
      return json(outcomes[act] ?? { applied: true, message: "Saved." });
    },
  };
}

const lanes = (posted: ReturnType<typeof recorder>): Record<string, Route> => ({
  "/intents": posted.route,
  "/actions/": posted.route,
});

function chatSink(): { sent: (string | FormData)[]; posted: string[]; route: Route } {
  const sent: (string | FormData)[] = [];
  const posted: string[] = [];
  return {
    sent,
    posted,
    route: (url, init) => {
      sent.push(init?.body as string | FormData);
      posted.push(url);
      return json(OPENED);
    },
  };
}

let chat = chatSink();

let acts = recorder();

async function built(expected = OPENING) {
  await screen.findByRole("heading", { name: "Creating your business’s workspace" });
  await userEvent.click(
    await screen.findByRole("button", { name: "Open your workspace" }, { timeout: BUILD_STEP_MS * 5 }),
  );
  expect(location.hash).toBe(HANDED);
  expect(chat.sent[0]).toEqual(expected);
  expect(chat.posted[0]).toContain("/agents/" + AGENT_ID + "/chat?conversation=new");
}

function next(): HTMLButtonElement {
  return screen.getByRole("button", { name: "Next" }) as HTMLButtonElement;
}

function counted(): (string | null)[] {
  const progress = screen.getByRole("progressbar", { name: "Step" });
  return [progress.getAttribute("aria-valuenow"), progress.getAttribute("aria-valuemax")];
}

async function press(group: string, on: string[]) {
  const options = within(await screen.findByRole("group", { name: group })).getAllByRole("button");
  for (const option of options) {
    const pressed = option.getAttribute("aria-pressed") === "true";
    if (pressed !== on.includes(option.textContent ?? "")) await userEvent.click(option);
  }
}

async function passWebsite() {
  await screen.findByRole("heading", { name: WEBSITE_HEADING });
  await userEvent.click(next());
}

async function describeBusiness(about = "Design studio") {
  await userEvent.type(await screen.findByLabelText("About your business"), about);
  await userEvent.click(next());
}

async function pickRoles(roles: string[] = ["Founder"]) {
  await press("Role", roles);
  await userEvent.click(next());
}

async function pickRole(role = "Founder") {
  await pickRoles([role]);
}

async function skipTools() {
  await screen.findByRole("heading", { name: "Which tools do you work in?" });
  await userEvent.click(next());
}

async function pickGoals(goals: string[] = ["Growing revenue"], other = "") {
  await screen.findByRole("heading", { name: "What is top of mind right now?" });
  await press("Top of mind", goals);
  if (other) await userEvent.type(screen.getByLabelText("What is top of mind"), other);
  await userEvent.click(next());
}

async function answer() {
  await passWebsite();
  await describeBusiness();
  await pickRole();
  await skipTools();
  await pickGoals();
}

async function returning() {
  for (const state of ["hidden", "visible"]) {
    Object.defineProperty(document, "visibilityState", { value: state, configurable: true });
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
  }
}

test("the run opens on the welcome, and Get started leads to the website question", async () => {
  mount();

  await screen.findByRole("heading", { name: "An AI operating system for your business" });
  expect(screen.getByText("Welcome to UFO")).toBeTruthy();
  expect(screen.getAllByRole("heading", { level: 2 }).map((point) => point.textContent)).toEqual([
    "Understand your business",
    "Build what your team needs",
    "Keep work moving",
  ]);
  expect(screen.queryByRole("progressbar", { name: "Step" })).toBeNull();
  expect(screen.queryByRole("banner")).toBeNull();
  expect(screen.queryByLabelText("Website")).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Get started" }));

  await screen.findByRole("heading", { name: WEBSITE_HEADING });
  expect(screen.queryByRole("button", { name: "Get started" })).toBeNull();
});

test("the welcome offers no way to close the run", async () => {
  mount();

  await screen.findByRole("button", { name: "Get started" });
  expect(screen.queryByRole("button", { name: "Close" })).toBeNull();
});

test("a step offers no way to close the run", async () => {
  await open(lanes(recorder()));

  await answer();
  await screen.findByRole("heading", { name: "Connect your messaging app" });

  expect(screen.queryByRole("button", { name: "Close" })).toBeNull();
  expect(location.hash).toBe(firstRunHash("slack"));
});

test("the card's query lands on the first run's own address", async () => {
  mount();

  await screen.findByRole("button", { name: "Get started" });
  expect(location.hash).toBe("#/first-run");
  await userEvent.click(screen.getByRole("button", { name: "Get started" }));

  await screen.findByRole("heading", { name: WEBSITE_HEADING });
  await waitFor(() => expect(location.hash).toBe("#/first-run/website"));
  expect(screen.queryByPlaceholderText("Start new chat…")).toBeNull();
});

test("the address opens the first run on its own, with no query at all", async () => {
  history.replaceState(null, "", location.pathname);
  location.hash = "#/first-run";
  await open();

  await screen.findByLabelText("Website");
  await waitFor(() => expect(location.hash).toBe("#/first-run/website"));
});

test("each step reached writes its own address, and Back from the first returns to the welcome", async () => {
  const entries = history.length;
  await open(lanes(recorder()));

  await screen.findByLabelText("Website");
  await waitFor(() => expect(location.hash).toBe(firstRunHash("website")));
  expect(destination()).toBe(WEBSITE_HEADING);
  await passWebsite();
  await screen.findByLabelText("About your business");
  expect(location.hash).toBe(firstRunHash("business"));
  expect(destination()).toBe("Tell us a bit about your business");
  await describeBusiness();
  await screen.findByRole("group", { name: "Role" });
  expect(location.hash).toBe(firstRunHash("position"));
  expect(destination()).toBe("What is your role at the business?");
  await pickRole();
  await screen.findByRole("heading", { name: "Which tools do you work in?" });
  expect(location.hash).toBe(firstRunHash("tools"));
  await skipTools();
  await screen.findByRole("heading", { name: "What is top of mind right now?" });
  expect(location.hash).toBe(firstRunHash("goals"));
  expect(destination()).toBe("What is top of mind right now?");

  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  await userEvent.click(await screen.findByRole("button", { name: "Back" }));
  await userEvent.click(await screen.findByRole("button", { name: "Back" }));
  await screen.findByLabelText("About your business");
  expect(location.hash).toBe(firstRunHash("business"));
  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  await screen.findByLabelText("Website");
  expect(location.hash).toBe(firstRunHash("website"));
  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  await screen.findByRole("button", { name: "Get started" });
  expect(location.hash).toBe("#/first-run");

  await userEvent.click(screen.getByRole("button", { name: "Get started" }));
  for (let step = 0; step < 5; step += 1) {
    await userEvent.click(await screen.findByRole("button", { name: "Next" }));
  }
  await screen.findByRole("heading", { name: "Connect your messaging app" });
  expect(location.hash).toBe(firstRunHash("slack"));
  expect(destination()).toBe("Connect your messaging app");
  expect(history.length).toBe(entries);
});

test("a reload on a step keeps the step and the answers under it", async () => {
  await open(lanes(recorder()));
  await passWebsite();
  await describeBusiness("A two-person design studio");
  await pickRole("Engineer");
  await screen.findByRole("heading", { name: "Which tools do you work in?" });
  await userEvent.click(screen.getByRole("button", { name: "GitHub" }));
  await userEvent.click(next());
  await screen.findByRole("heading", { name: "Connect the tools you picked" });
  await userEvent.click(next());
  await screen.findByRole("heading", { name: "What is top of mind right now?" });
  await userEvent.click(screen.getByRole("button", { name: "Hiring" }));
  expect(location.hash).toBe(firstRunHash("goals"));

  cleanup();
  mount(lanes(recorder()));

  expect(
    (await screen.findByRole("button", { name: "Hiring" })).getAttribute("data-state"),
  ).toBe("on");
  expect(counted()).toEqual(["5", "6"]);
  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  expect(
    (await screen.findByRole("button", { name: "GitHub" })).getAttribute("data-state"),
  ).toBe("on");
  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  expect(
    (await screen.findByRole("button", { name: "Engineer" })).getAttribute("aria-pressed"),
  ).toBe("true");
  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  expect(((await screen.findByLabelText("About your business")) as HTMLTextAreaElement).value).toBe(
    "A two-person design studio",
  );
});

test("the Slack step's address with the answers held lands on the Slack step, counted", async () => {
  sessionStorage.setItem(
    ANSWERS_KEY,
    JSON.stringify({ business: "Design studio", roles: ["Founder"], goals: [] }),
  );
  history.replaceState(null, "", location.pathname);
  location.hash = firstRunHash("slack");
  mount(lanes(recorder()));

  await screen.findByRole("heading", { name: "Connect your messaging app" });
  expect(counted()).toEqual(["6", "6"]);
  expect(screen.queryByRole("button", { name: "Get started" })).toBeNull();
});

test("an address naming no step of the run lands on the first step, and is written over", async () => {
  history.replaceState(null, "", location.pathname);
  location.hash = "#/first-run/nonsense";
  mount();

  await screen.findByLabelText("Website");
  expect(counted()).toEqual(["1", "6"]);
  await waitFor(() => expect(location.hash).toBe(firstRunHash("website")));
});

test("finishing the run drops the answers it held", async () => {
  await open(lanes(recorder()), ADMIN, NO_SLACK);
  await passWebsite();
  await describeBusiness();
  expect(stored()?.business).toBe("Design studio");
  await pickRole();
  await skipTools();
  await pickGoals();

  await built();
  expect(stored()).toBeNull();
});

test("the page draws no shell around the step", async () => {
  await open();

  await screen.findByRole("heading", { name: WEBSITE_HEADING });
  expect(screen.queryByRole("banner")).toBeNull();
  expect(screen.queryByRole("button", { name: "Menu" })).toBeNull();
});

test("the head counts six steps with Slack, advancing one per answer under a single headline", async () => {
  await open(lanes(recorder()));

  const alone = () => {
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading.parentElement!.querySelectorAll("p").length).toBe(0);
    expect(screen.queryByRole("button", { name: "Close" })).toBeNull();
  };

  await screen.findByLabelText("Website");
  expect(counted()).toEqual(["1", "6"]);
  expect(screen.getByRole("progressbar", { name: "Step" }).childElementCount).toBe(6);
  alone();

  await passWebsite();
  await screen.findByRole("heading", { name: "Tell us a bit about your business" });
  expect(counted()).toEqual(["2", "6"]);
  alone();

  await describeBusiness();
  await screen.findByRole("heading", { name: "What is your role at the business?" });
  expect(counted()).toEqual(["3", "6"]);
  expect(screen.queryByText("Pick every one that applies.")).toBeNull();
  alone();

  await pickRole();
  await screen.findByRole("heading", { name: "Which tools do you work in?" });
  expect(counted()).toEqual(["4", "6"]);
  alone();

  await skipTools();
  await screen.findByRole("heading", { name: "What is top of mind right now?" });
  expect(counted()).toEqual(["5", "6"]);
  alone();

  await pickGoals();
  await screen.findByRole("heading", { name: "Connect your messaging app" });
  expect(counted()).toEqual(["6", "6"]);
  expect(screen.queryByRole("button", { name: "Close" })).toBeNull();
});

test("a deploy without Slack counts five steps, and the fifth's Next finishes the run", async () => {
  await open(lanes(recorder()), ADMIN, NO_SLACK);

  await screen.findByLabelText("Website");
  expect(counted()).toEqual(["1", "5"]);
  expect(screen.getByRole("progressbar", { name: "Step" }).childElementCount).toBe(5);

  await passWebsite();
  await describeBusiness();
  await pickRole();
  await skipTools();
  await screen.findByRole("heading", { name: "What is top of mind right now?" });
  expect(counted()).toEqual(["5", "5"]);
  await userEvent.click(next());

  await built();
  expect(screen.queryByRole("heading", { name: "Connect your messaging app" })).toBeNull();
});

test("a catalog carrying only Slack stands no tools step, and the run counts five", async () => {
  await open(lanes(recorder()), ADMIN, SLACK_ONLY);

  await screen.findByLabelText("Website");
  expect(counted()).toEqual(["1", "5"]);
  await passWebsite();
  await describeBusiness();
  await pickRole("Engineer");

  await screen.findByRole("heading", { name: "What is top of mind right now?" });
  expect(counted()).toEqual(["4", "5"]);
});

test("Cmd+Enter is Next on every question, and holds where Next is disabled", async () => {
  await open();

  await screen.findByLabelText("Website");
  await userEvent.keyboard("{Meta>}{Enter}{/Meta}");
  const about = await screen.findByLabelText("About your business");
  await userEvent.keyboard("{Meta>}{Enter}{/Meta}");
  expect(screen.queryByRole("heading", { name: "What is your role at the business?" })).toBeNull();

  await userEvent.type(about, "Design studio");
  await userEvent.keyboard("{Meta>}{Enter}{/Meta}");
  await screen.findByRole("heading", { name: "What is your role at the business?" });

  await userEvent.keyboard("{Control>}{Enter}{/Control}");
  await screen.findByRole("heading", { name: "Which tools do you work in?" });
  await userEvent.keyboard("{Meta>}{Enter}{/Meta}");
  await screen.findByRole("heading", { name: "What is top of mind right now?" });

  await userEvent.click(screen.getByRole("button", { name: "Other" }));
  await userEvent.keyboard("{Meta>}{Enter}{/Meta}");
  expect(screen.getByRole("heading", { name: "What is top of mind right now?" })).toBeTruthy();
});

test("the business step gates Next on the business and names who is answering", async () => {
  await open();
  await passWebsite();

  const about = await screen.findByLabelText("About your business");
  expect(about).toBeInstanceOf(HTMLTextAreaElement);
  expect(about.getAttribute("placeholder")).toBe("What does your business do, and who is it for?");
  expect(screen.getByText(ADMIN.email)).toBeTruthy();
  expect(next().disabled).toBe(true);

  await userEvent.type(about, "  ");
  expect(next().disabled).toBe(true);
  await userEvent.type(about, "Design studio");
  expect(next().disabled).toBe(false);

  await userEvent.click(next());
  await screen.findByRole("heading", { name: "What is your role at the business?" });
});

test("the role step offers the twelve roles as a set, presses Founder until told otherwise, and Other asks for words", async () => {
  await open();
  await passWebsite();
  await describeBusiness();

  const group = await screen.findByRole("group", { name: "Role" });
  const roles = within(group).getAllByRole("button");
  expect(roles.map((role) => role.textContent)).toEqual(ROLES);
  const pressed = () =>
    within(group)
      .queryAllByRole("button", { pressed: true })
      .map((role) => role.textContent);
  expect(pressed()).toEqual(["Founder"]);
  expect(next().disabled).toBe(false);
  expect(screen.queryByLabelText("Your role")).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Sales" }));
  expect(pressed()).toEqual(["Founder", "Sales"]);
  await userEvent.click(screen.getByRole("button", { name: "Founder" }));
  expect(pressed()).toEqual(["Sales"]);
  await userEvent.click(screen.getByRole("button", { name: "Sales" }));
  expect(pressed()).toEqual([]);
  expect(next().disabled).toBe(true);

  await userEvent.click(screen.getByRole("button", { name: "Other" }));
  const own = screen.getByLabelText("Your role");
  expect(document.activeElement).toBe(own);
  expect(next().disabled).toBe(true);
  await userEvent.type(own, "Barista");
  expect(next().disabled).toBe(false);
});

test("the tools step offers the connectors the picked role works in", async () => {
  await open(lanes(recorder()));
  await passWebsite();
  await describeBusiness();
  await pickRole("Engineer");

  await screen.findByRole("heading", { name: "Which tools do you work in?" });
  expect(counted()).toEqual(["4", "6"]);
  const tools = within(screen.getByRole("group", { name: "Tools" })).getAllByRole("button");
  expect(tools.map((tool) => tool.textContent)).toEqual(["GitHub"]);
  expect(tools.map((tool) => tool.getAttribute("aria-pressed"))).toEqual(["false"]);
  expect(next().disabled).toBe(false);
});

test("the tools step ranks the picked roles' tools ahead of the rest of the catalog", async () => {
  await open(lanes(recorder()), ADMIN, RANKED);
  await passWebsite();
  await describeBusiness();
  await pickRoles(["Designer", "Engineer"]);

  await screen.findByRole("heading", { name: "Which tools do you work in?" });
  const tools = within(screen.getByRole("group", { name: "Tools" })).getAllByRole("button");
  expect(tools.map((tool) => tool.textContent)).toEqual(["Notion", "Linear", "GitHub", "Gmail"]);
  expect(screen.queryByRole("button", { name: "Show more" })).toBeNull();
});

test("the founder's own tools name GitHub ahead of the rest of the catalog", async () => {
  await open(lanes(recorder()), ADMIN, RANKED);
  await passWebsite();
  await describeBusiness();
  await pickRole("Founder");

  await screen.findByRole("heading", { name: "Which tools do you work in?" });
  const tools = within(screen.getByRole("group", { name: "Tools" })).getAllByRole("button");
  expect(tools.map((tool) => tool.textContent)).toEqual(["Gmail", "Notion", "GitHub", "Linear"]);
});

test("the tools step skips to the goals where nothing is picked", async () => {
  const posted = recorder();
  await open(lanes(posted));
  await passWebsite();
  await describeBusiness();
  await pickRole("Engineer");

  await screen.findByRole("heading", { name: "Which tools do you work in?" });
  await userEvent.click(next());

  await screen.findByRole("heading", { name: "What is top of mind right now?" });
  expect(intents(posted.calls)).toEqual([]);
});

test("a picked tool is stood to connect, and the press asks for that account", async () => {
  const posted = recorder();
  await open(lanes(posted));
  const consent = { focus: vi.fn(), close: vi.fn(), location: { href: "" } };
  const opened = vi.spyOn(window, "open").mockReturnValue(consent as unknown as Window);
  onTestFinished(() => opened.mockRestore());

  await passWebsite();
  await describeBusiness();
  await pickRole("Engineer");
  await screen.findByRole("heading", { name: "Which tools do you work in?" });
  await userEvent.click(screen.getByRole("button", { name: "GitHub" }));
  await userEvent.click(next());

  await screen.findByRole("heading", { name: "Connect the tools you picked" });
  await userEvent.click(screen.getByRole("button", { name: "Connect GitHub" }));

  await waitFor(() =>
    expect(intents(posted.calls)).toEqual([
      {
        lane: "intents",
        body: { verb: "connect", kind: "connection", name: "github", spec: { shared: false } },
      },
    ]),
  );
});

test("Next off the connect list carries on to the goals", async () => {
  await open(lanes(recorder()));
  await passWebsite();
  await describeBusiness();
  await pickRole("Engineer");
  await screen.findByRole("heading", { name: "Which tools do you work in?" });
  await userEvent.click(screen.getByRole("button", { name: "GitHub" }));
  await userEvent.click(next());

  await screen.findByRole("heading", { name: "Connect the tools you picked" });
  await userEvent.click(next());

  await screen.findByRole("heading", { name: "What is top of mind right now?" });
});

test("the top-of-mind step offers the nine goals with Growing revenue pressed, and Other asks for words", async () => {
  await open();
  await passWebsite();
  await describeBusiness();
  await pickRole();
  await skipTools();

  await screen.findByRole("heading", { name: "What is top of mind right now?" });
  const goals = within(screen.getByRole("group", { name: "Top of mind" })).getAllByRole("button");
  expect(goals.map((goal) => goal.textContent)).toEqual(GOALS);
  expect(goals.map((goal) => goal.getAttribute("aria-pressed"))).toEqual(
    GOALS.map((goal) => String(goal === "Growing revenue")),
  );
  expect(next().disabled).toBe(false);
  expect(screen.queryByLabelText("What is top of mind")).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Other" }));
  const own = screen.getByLabelText("What is top of mind");
  expect(document.activeElement).toBe(own);
  expect(next().disabled).toBe(true);
  await userEvent.type(own, "  ");
  expect(next().disabled).toBe(true);
  await userEvent.type(own, "Launching in Japan");
  expect(next().disabled).toBe(false);
});

test("back walks the answers without losing one", async () => {
  await open();
  await passWebsite();
  await describeBusiness();
  await pickRole("Engineer");
  await skipTools();
  await screen.findByRole("heading", { name: "What is top of mind right now?" });
  await userEvent.click(screen.getByRole("button", { name: "Hiring" }));

  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  await screen.findByRole("heading", { name: "Which tools do you work in?" });
  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  expect((await screen.findByRole("button", { name: "Engineer" })).getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByRole("button", { name: "Founder" }).getAttribute("aria-pressed")).toBe("false");
  expect(counted()).toEqual(["3", "6"]);

  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  expect(((await screen.findByLabelText("About your business")) as HTMLTextAreaElement).value).toBe(
    "Design studio",
  );
  expect(counted()).toEqual(["2", "6"]);

  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  expect(((await screen.findByLabelText("Website")) as HTMLInputElement).value).toBe(DOMAIN);
  expect(counted()).toEqual(["1", "6"]);

  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  await screen.findByRole("heading", { name: "An AI operating system for your business" });
  await userEvent.click(screen.getByRole("button", { name: "Get started" }));
  expect(((await screen.findByLabelText("Website")) as HTMLInputElement).value).toBe(DOMAIN);

  await userEvent.click(next());
  expect(((await screen.findByLabelText("About your business")) as HTMLTextAreaElement).value).toBe(
    "Design studio",
  );
  await userEvent.click(next());
  await screen.findByRole("group", { name: "Role" });
  await userEvent.click(next());
  await screen.findByRole("heading", { name: "Which tools do you work in?" });
  await userEvent.click(next());

  await screen.findByRole("heading", { name: "What is top of mind right now?" });
  expect(screen.getByRole("button", { name: "Hiring" }).getAttribute("aria-pressed")).toBe("true");
});

test("the Slack step states what installing does and offers the admin the act", async () => {
  await open(lanes(recorder()));

  await answer();

  await screen.findByRole("heading", { name: "Connect your messaging app" });
  expect(screen.getByText("Get the power of UFO everywhere")).toBeTruthy();
  expect(screen.getByText("This will install UFO in Slack for your team")).toBeTruthy();
  expect(screen.getAllByRole("listitem").map((point) => point.textContent)).toEqual(SLACK_POINTS);
  expect(screen.getByRole("button", { name: "Connect Slack" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "I don't use Slack" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Next" })).toBeNull();
});

test("one press opens the consent window and lands the minted link in it", async () => {
  const posted = recorder({ slack_connect: { applied: true, message: "", url: SLACK_LINK } });
  await open(lanes(posted));
  const consent = { focus: vi.fn(), close: vi.fn(), location: { href: "" } };
  const opened = vi.spyOn(window, "open").mockReturnValue(consent as unknown as Window);

  await answer();
  await userEvent.click(await screen.findByRole("button", { name: "Connect Slack" }));

  const [url, name, features] = opened.mock.calls[0];
  expect(url).toBe("");
  expect(name).toBe("ufo-connect");
  expect(features).toContain("popup");
  await waitFor(() => expect(consent.location.href).toBe(SLACK_LINK));
  expect(screen.queryByRole("link", { name: "Open the Slack install page" })).toBeNull();
  opened.mockRestore();
});

test("a browser that refuses the window still hands the member the link", async () => {
  const posted = recorder({ slack_connect: { applied: true, message: "", url: SLACK_LINK } });
  await open(lanes(posted));

  await answer();
  await userEvent.click(await screen.findByRole("button", { name: "Connect Slack" }));

  const link = await screen.findByRole("link", { name: "Open the Slack install page" });
  expect(link.getAttribute("href")).toBe(SLACK_LINK);
  expect(intents(posted.calls)).toEqual([{ lane: "actions/surface/slack/slack_connect", body: {} }]);
  expect(location.hash).toBe(firstRunHash("slack"));
  expect(screen.queryByPlaceholderText("Start new chat…")).toBeNull();
});

test("a refused connect closes the window it opened and states the refusal", async () => {
  const posted = recorder({
    slack_connect: { applied: false, message: "Only a workspace admin installs Slack." },
  });
  await open(lanes(posted));
  const consent = { focus: vi.fn(), close: vi.fn(), location: { href: "" } };
  const opened = vi.spyOn(window, "open").mockReturnValue(consent as unknown as Window);

  await answer();
  await userEvent.click(await screen.findByRole("button", { name: "Connect Slack" }));

  await screen.findByText("Only a workspace admin installs Slack.");
  expect(consent.close).toHaveBeenCalled();
  expect(consent.location.href).toBe("");
  expect(screen.queryByRole("link")).toBeNull();
  opened.mockRestore();
});

test("a member who is not an admin is told who installs, and finishes the run past it", async () => {
  await open(lanes(recorder()), MEMBER);

  await answer();

  await screen.findByText("A workspace admin connects Slack.");
  expect(screen.queryByRole("button", { name: "Connect Slack" })).toBeNull();
  expect(screen.queryByRole("button", { name: "I don't use Slack" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));

  await built();
});

test("a connector the workspace already holds opens on the success screen, and Continue finishes", async () => {
  await open(lanes(recorder()), ADMIN, HELD_SLACK);

  await answer();

  await screen.findByRole("heading", { name: "We were able to connect to Slack" });
  expect(screen.getByText("This installed UFO in Slack for your team")).toBeTruthy();
  expect(screen.getByText("Success")).toBeTruthy();
  expect(counted()).toEqual(["6", "6"]);
  expect(screen.queryByRole("button", { name: "Connect Slack" })).toBeNull();
  await returning();
  expect(screen.getByRole("heading", { name: "We were able to connect to Slack" })).toBeTruthy();
  expect(chat.sent).toEqual([OPENING]);

  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  await built();
});

test("the member coming back from Slack's pages lands on the success screen, and Continue finishes", async () => {
  const posted = recorder({ slack_connect: { applied: true, message: "", url: SLACK_LINK } });
  let landed = false;
  await open({
    ...lanes(posted),
    "/workspace/first-run": () => json(landed ? HELD_SLACK : FIRST_RUN),
  });

  await answer();
  await userEvent.click(await screen.findByRole("button", { name: "Connect Slack" }));
  await screen.findByRole("link", { name: "Open the Slack install page" });
  await returning();
  expect(screen.getByRole("heading", { name: "Connect your messaging app" })).toBeTruthy();
  expect(chat.sent).toEqual([OPENING]);

  landed = true;
  await returning();

  await screen.findByRole("heading", { name: "We were able to connect to Slack" });
  expect(chat.sent).toEqual([OPENING]);
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  await built();
});

test("the step's own watch lands the success screen while the tab stays open", async () => {
  vi.useFakeTimers();
  onTestFinished(() => {
    vi.useRealTimers();
  });
  const posted = recorder({ slack_connect: { applied: true, message: "", url: SLACK_LINK } });
  let landed = false;
  mount({
    ...lanes(posted),
    "/workspace/first-run": () => json(landed ? HELD_SLACK : FIRST_RUN),
  });
  const settle = () => act(async () => void (await vi.advanceTimersByTimeAsync(0)));

  await settle();
  fireEvent.click(screen.getByRole("button", { name: "Get started" }));
  await settle();
  fireEvent.click(next());
  fireEvent.change(screen.getByLabelText("About your business"), {
    target: { value: "Design studio" },
  });
  fireEvent.click(next());
  fireEvent.click(next());
  fireEvent.click(next());
  fireEvent.click(next());
  fireEvent.click(screen.getByRole("button", { name: "Connect Slack" }));
  await settle();
  expect(screen.getByRole("link", { name: "Open the Slack install page" })).toBeTruthy();

  landed = true;
  await act(async () => void (await vi.advanceTimersByTimeAsync(WATCH_MS)));
  await settle();

  expect(screen.getByRole("heading", { name: "We were able to connect to Slack" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "Connect your messaging app" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  await settle();
  expect(screen.getByRole("heading", { name: "Creating your business’s workspace" })).toBeTruthy();
  for (let step = 0; step < 3; step += 1) {
    await act(async () => void (await vi.advanceTimersByTimeAsync(BUILD_STEP_MS)));
  }
  fireEvent.click(screen.getByRole("button", { name: "Open your workspace" }));
  await settle();
  expect(location.hash).toBe(HANDED);
  expect(chat.sent).toEqual(DEFAULT_SENT);
});

test("the Slack step's Continue finishes the run with the answers written, as one sentence", async () => {
  const posted = recorder();
  await open(lanes(posted), ADMIN, HELD_SLACK);

  await passWebsite();
  await describeBusiness("A two-person design studio.");
  await pickRole();
  await skipTools();
  await pickGoals();

  await screen.findByRole("heading", { name: "We were able to connect to Slack" });
  expect(counted()).toEqual(["6", "6"]);
  expect(chat.sent).toEqual([
    "I just set up this workspace. My business: A two-person design studio. Set up my first task: " +
      "a daily competitive analysis.",
  ]);
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));

  await built(
    "I just set up this workspace. My business: A two-person design studio. Set up my first task: a daily competitive analysis.",
  );
  expect(intents(posted.calls)).toEqual([
    {
      lane: "actions/memory/record_first_run",
      body: {
        body:
          "A two-person design studio. Their website: " +
          DOMAIN +
          ". Their role: Founder. Their goals: growing revenue.",
      },
    },
  ]);
});

test("the last screen lands home on the founded thread with connectors beside it", async () => {
  await open(lanes(recorder()), ADMIN, NO_SLACK);

  await answer();

  await built();
  expect(location.hash).toBe(HANDED);
  await waitFor(() =>
    expect(
      Array.from(document.querySelectorAll("[data-slot=slot-track] > div > section")).map((lane) =>
        lane.getAttribute("aria-label"),
      ),
    ).toEqual(["Conversation", "Connections"]),
  );
});

test("the opening line is said to the chat app home stands, not the agent the run was drawn for", async () => {
  await open(lanes(recorder()), ADMIN, NO_SLACK, [AGENT, CHAT_APP]);

  await passWebsite();
  await describeBusiness();

  await waitFor(() => expect(chat.sent).toEqual([OPENING]));
  expect(chat.posted[0]).toContain("/agents/" + CHAT_APP_ID + "/chat?conversation=new");
});

test("a second run through opens a new conversation rather than the one the first founded", async () => {
  await open(lanes(recorder()), ADMIN, NO_SLACK);
  await answer();
  await built();

  location.hash = "#/first-run";
  window.dispatchEvent(new HashChangeEvent("hashchange"));
  await userEvent.click(await screen.findByRole("button", { name: "Get started" }));
  await answer();
  await screen.findByRole("heading", { name: "Creating your business’s workspace" });
  await userEvent.click(
    await screen.findByRole("button", { name: "Open your workspace" }, { timeout: BUILD_STEP_MS * 5 }),
  );

  await waitFor(() => expect(chat.sent).toEqual([...DEFAULT_SENT, ...DEFAULT_SENT]));
  expect(chat.posted).toEqual(chat.posted.map(() => chat.posted[0]));
  expect(chat.posted[2]).toContain("conversation=new");
}, BUILD_STEP_MS * 20);

test("the website step opens the run, prefilled with the workspace's domain, and Back returns to the welcome", async () => {
  await open();

  await screen.findByRole("heading", { name: WEBSITE_HEADING });
  const website = screen.getByLabelText("Website") as HTMLInputElement;
  expect(website.value).toBe(DOMAIN);
  expect(counted()).toEqual(["1", "6"]);
  expect(screen.queryByLabelText("About your business")).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  await screen.findByRole("heading", { name: "An AI operating system for your business" });
  expect(screen.queryByRole("progressbar", { name: "Step" })).toBeNull();
});

test("a workspace with no domain opens the website box empty, and Next posts nothing without the act", async () => {
  const posted = recorder();
  await open(lanes(posted), ADMIN, { ...FIRST_RUN, workspace_domain: null });

  await screen.findByRole("heading", { name: WEBSITE_HEADING });
  expect((screen.getByLabelText("Website") as HTMLInputElement).value).toBe("");
  expect(screen.queryByRole("button", { name: "Clear" })).toBeNull();
  expect(next().disabled).toBe(false);
  await userEvent.click(next());

  await screen.findByRole("heading", { name: "Tell us a bit about your business" });
  expect(intents(posted.calls)).toEqual([]);
});

test("a website the member typed survives a reload rather than being written over by the domain", async () => {
  await open();

  await screen.findByRole("heading", { name: WEBSITE_HEADING });
  const website = screen.getByLabelText("Website");
  await userEvent.clear(website);
  await userEvent.type(website, "beta.co");

  cleanup();
  mount();
  expect(((await screen.findByLabelText("Website")) as HTMLInputElement).value).toBe("beta.co");
});

test("a cleared website stays cleared across a reload", async () => {
  await open();

  await screen.findByRole("heading", { name: WEBSITE_HEADING });
  await userEvent.click(screen.getByRole("button", { name: "Clear" }));
  expect((screen.getByLabelText("Website") as HTMLInputElement).value).toBe("");

  cleanup();
  mount();
  expect(((await screen.findByLabelText("Website")) as HTMLInputElement).value).toBe("");
});

test("clearing the website empties the field, Next posts an empty website, and the business box is left empty", async () => {
  const posted = recorder();
  await open(lanes(posted), ADMIN, WITH_WEBSITE);

  await screen.findByRole("heading", { name: WEBSITE_HEADING });
  const website = screen.getByLabelText("Website") as HTMLInputElement;

  await userEvent.click(screen.getByRole("button", { name: "Clear" }));
  expect(website.value).toBe("");
  expect(screen.queryByRole("button", { name: "Clear" })).toBeNull();
  await userEvent.click(next());

  await screen.findByRole("heading", { name: "Tell us a bit about your business" });
  expect(((await screen.findByLabelText("About your business")) as HTMLTextAreaElement).value).toBe(
    "",
  );
  expect(counted()).toEqual(["2", "6"]);
  expect(intents(posted.calls)).toEqual([
    { lane: "actions/enrichment_profile/confirm_website", body: { website: "" } },
  ]);
});

test("a matched website writes the business box once the job has written the row", async () => {
  let rows: unknown[] = [];
  await open(
    {
      ...lanes(recorder()),
      "/objects/enrichment_profile": () => json({ objects: rows, next_cursor: null }),
    },
    ADMIN,
    WITH_WEBSITE,
  );

  await passWebsite();

  const about = (await screen.findByLabelText("About your business")) as HTMLTextAreaElement;
  expect(about.value).toBe("");

  rows = [PROFILE_ROW];
  await waitFor(() => expect(about.value).toBe(LEARNED), { timeout: WATCH_MS * 4 });
  expect(next().disabled).toBe(false);

  await userEvent.clear(about);
  await userEvent.type(about, "A two-person design studio");
  expect(about.value).toBe("A two-person design studio");
});

test("a row that lands after the member has typed does not overwrite them", async () => {
  let rows: unknown[] = [];
  await open(
    {
      ...lanes(recorder()),
      "/objects/enrichment_profile": () => json({ objects: rows, next_cursor: null }),
    },
    ADMIN,
    WITH_WEBSITE,
  );

  await passWebsite();
  const about = (await screen.findByLabelText("About your business")) as HTMLTextAreaElement;
  await userEvent.type(about, "A two-person design studio");

  rows = [PROFILE_ROW];
  await new Promise((done) => setTimeout(done, WATCH_MS * 2));
  expect(about.value).toBe("A two-person design studio");
}, WATCH_MS * 4);

test("confirming another website waits for that website's profile", async () => {
  let rows: unknown[] = [PROFILE_ROW];
  await open(
    {
      ...lanes(recorder()),
      "/objects/enrichment_profile": () => json({ objects: rows, next_cursor: null }),
    },
    ADMIN,
    WITH_WEBSITE,
  );

  await passWebsite();
  const about = (await screen.findByLabelText("About your business")) as HTMLTextAreaElement;
  await waitFor(() => expect(about.value).toBe(LEARNED));

  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  const website = await screen.findByLabelText("Website");
  await userEvent.clear(website);
  await userEvent.type(website, "beta.co");
  rows = [
    {
      ...PROFILE_ROW,
      company_name: "Beta",
      company_industry: "Robotics",
      company_summary: "Robots for warehouses.",
    },
  ];
  await userEvent.click(next());

  const betaAbout = (await screen.findByLabelText("About your business")) as HTMLTextAreaElement;
  await waitFor(() => expect(betaAbout.value).toContain("Robots for warehouses."));
  expect(betaAbout.value).not.toContain("keep it simple");
});

test("a profile that lands after the role step does not replace the confirmed roles", async () => {
  let rows: unknown[] = [];
  await open(
    {
      ...lanes(recorder()),
      "/objects/enrichment_profile": () => json({ objects: rows, next_cursor: null }),
    },
    ADMIN,
    WITH_WEBSITE,
  );

  await passWebsite();
  await describeBusiness();
  await screen.findByRole("heading", { name: "What is your role at the business?" });
  await userEvent.click(next());
  await screen.findByRole("heading", { name: "Which tools do you work in?" });

  rows = [{ ...PROFILE_ROW, job_title_levels: null, job_title_role: "engineering" }];
  await returning();
  await waitFor(() => expect(stored()?.profile).toBeTruthy());
  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  expect(screen.getByRole("button", { name: "Founder" }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByRole("button", { name: "Engineer" }).getAttribute("aria-pressed")).toBe("false");
});

test("confirming posts the website and pre-selects Founder, and no step names the company", async () => {
  const posted = recorder();
  await open({ ...lanes(posted), ...PROFILE_READ }, ADMIN, WITH_WEBSITE);

  await passWebsite();
  await describeBusiness();

  const founder = await screen.findByRole("button", { name: "Founder" });
  expect(founder.getAttribute("aria-pressed")).toBe("true");
  expect(next().disabled).toBe(false);
  expect(intents(posted.calls)).toEqual([
    { lane: "actions/enrichment_profile/confirm_website", body: { website: DOMAIN } },
  ]);
  expect(screen.queryByText("Simplecasual")).toBeNull();
  await userEvent.click(next());
  await skipTools();

  await screen.findByRole("heading", { name: "What is top of mind right now?" });
  expect(screen.queryByText("Simplecasual")).toBeNull();
});

test("a refused confirmation is stated as a toast and holds the website step", async () => {
  const posted = recorder({ confirm_website: { applied: false, message: "No key." } });
  await open({ ...lanes(posted), ...PROFILE_READ }, ADMIN, WITH_WEBSITE);

  await screen.findByRole("heading", { name: WEBSITE_HEADING });
  await userEvent.click(next());

  await screen.findByText("No key.");
  expect(screen.getByRole("heading", { name: WEBSITE_HEADING })).toBeTruthy();
  expect(screen.queryByLabelText("About your business")).toBeNull();
});

test("each question opens with its own input focused", async () => {
  await open({ ...lanes(recorder()), ...PROFILE_READ }, ADMIN, WITH_WEBSITE);

  expect(document.activeElement).toBe(await screen.findByLabelText("Website"));
  await userEvent.click(next());
  expect(document.activeElement).toBe(await screen.findByLabelText("About your business"));
  await describeBusiness();
  expect(document.activeElement).toBe(await screen.findByRole("button", { name: "Founder" }));
  await userEvent.click(next());
  await skipTools();
  await userEvent.click(await screen.findByRole("button", { name: "Other" }));
  expect(document.activeElement).toBe(screen.getByLabelText("What is top of mind"));
});

test("the goal the step opens on is unpicked and picked again", async () => {
  await open();
  await passWebsite();
  await describeBusiness();
  await pickRole();
  await skipTools();

  await screen.findByRole("heading", { name: "What is top of mind right now?" });
  const revenue = screen.getByRole("button", { name: "Growing revenue" });
  expect(revenue.getAttribute("aria-pressed")).toBe("true");
  await userEvent.click(revenue);
  expect(revenue.getAttribute("aria-pressed")).toBe("false");
  await userEvent.click(revenue);
  expect(revenue.getAttribute("aria-pressed")).toBe("true");
});

test("the run writes the business, the website, the role and the goals to memory before it hands over", async () => {
  await open({}, ADMIN, NO_SLACK);
  await passWebsite();
  await describeBusiness();
  await pickRole();
  await skipTools();
  await pickGoals(["Growing revenue", "Fundraising"]);
  await built();

  expect(intents(acts.calls)).toEqual([
    {
      lane: "actions/memory/record_first_run",
      body: { body: WHO + " Their goals: growing revenue, fundraising." },
    },
  ]);
});

test("several roles are joined in memory and in every goal thread", async () => {
  await open({}, ADMIN, NO_SLACK);
  await passWebsite();
  await describeBusiness();
  await pickRoles(["Founder", "Designer"]);
  await skipTools();
  await pickGoals();

  await screen.findByRole("heading", { name: "Creating your business’s workspace" });
  expect(chat.sent).toEqual([OPENING, opens("Founder / Designer", "growing revenue") + REVENUE_ASKS]);
  await built();
  expect(intents(acts.calls)).toEqual([
    {
      lane: "actions/memory/record_first_run",
      body: {
        body:
          "Design studio. Their website: " +
          DOMAIN +
          ". Their role: Founder / Designer. Their goals: growing revenue.",
      },
    },
  ]);
});

test("Other's words stand in for it among the roles", async () => {
  await open({}, ADMIN, NO_SLACK);
  await passWebsite();
  await describeBusiness();
  await press("Role", ["Founder", "Other"]);
  await userEvent.type(screen.getByLabelText("Your role"), "Barista");
  await userEvent.click(next());
  await skipTools();
  await pickGoals([]);
  await built();

  expect(intents(acts.calls)).toEqual([
    {
      lane: "actions/memory/record_first_run",
      body: { body: "Design studio. Their website: " + DOMAIN + ". Their role: Founder / Barista." },
    },
  ]);
});

test("a run with no website writes none", async () => {
  await open({}, ADMIN, { ...NO_SLACK, workspace_domain: null });
  await passWebsite();
  await describeBusiness();
  await pickRole();
  await skipTools();
  await pickGoals();
  await built();

  expect(intents(acts.calls)).toEqual([
    {
      lane: "actions/memory/record_first_run",
      body: { body: "Design studio. Their role: Founder. Their goals: growing revenue." },
    },
  ]);
});

test("Other's words are written to memory as the member typed them, and open a thread of their own", async () => {
  await open({}, ADMIN, NO_SLACK);
  await passWebsite();
  await describeBusiness();
  await pickRole();
  await skipTools();
  await pickGoals(["Hiring", "Other"], "Launching in Japan.");

  await screen.findByRole("heading", { name: "Creating your business’s workspace" });
  expect(chat.sent).toEqual([OPENING, HIRING_THREAD, OTHER_THREAD]);
  await built();
  expect(intents(acts.calls)).toEqual([
    {
      lane: "actions/memory/record_first_run",
      body: { body: WHO + " Their goals: hiring, Launching in Japan." },
    },
  ]);
});

test("a run with no goal picked still writes who the workspace is for", async () => {
  await open({}, ADMIN, NO_SLACK);
  await passWebsite();
  await describeBusiness();
  await pickRole();
  await skipTools();
  await pickGoals([]);
  await built();

  expect(chat.sent).toEqual([OPENING]);
  expect(intents(acts.calls)).toEqual([{ lane: "actions/memory/record_first_run", body: { body: WHO } }]);
});

test("the pressed goal and a picked one each open a thread after the first task's", async () => {
  await open({}, ADMIN, NO_SLACK);
  await passWebsite();
  await describeBusiness();
  await pickRole();
  await skipTools();
  await pickGoals(["Growing revenue", "Understanding competitors"]);

  await screen.findByRole("heading", { name: "Creating your business’s workspace" });
  expect(chat.sent).toEqual([OPENING, REVENUE_THREAD, COMPETITORS_THREAD]);
  for (const url of chat.posted) {
    expect(url).toContain("/agents/" + AGENT_ID + "/chat?conversation=new");
  }
});

test("the workspace screen names the goals it opened a thread on", async () => {
  await open({}, ADMIN, NO_SLACK);
  await answer();

  await screen.findByRole("heading", { name: "Creating your business’s workspace" });
  await screen.findByText("Started work on your goals", {}, { timeout: BUILD_STEP_MS * 6 });
  expect(screen.getByText("Growing revenue")).toBeTruthy();
});

test("the build screen states when a goal thread did not start", async () => {
  await open(
    {
      ...lanes(recorder()),
      "/chat": () => new Response(null, { status: 500 }),
    },
    ADMIN,
    NO_SLACK,
  );
  await answer();

  expect(
    await screen.findByRole("heading", { name: "Creating your business’s workspace" }),
  ).toBeTruthy();
  expect(
    await screen.findByText("No thread started for Growing revenue. Ask for it in chat."),
  ).toBeTruthy();
});

test("a refused memory write holds the last step and states the refusal", async () => {
  const posted = recorder({ record_first_run: { applied: false, message: "Memory is full." } });
  await open(lanes(posted), ADMIN, NO_SLACK);
  await answer();

  await screen.findByText("Memory is full.");
  expect(screen.queryByRole("heading", { name: "Creating your business’s workspace" })).toBeNull();
  expect(chat.sent).toEqual([OPENING]);
});

test("a deploy running without memory refuses the last step rather than handing over", async () => {
  await open({}, ADMIN, { ...NO_SLACK, actions: { ...ACTIONS.actions, memory: [] } });
  await answer();

  await screen.findByText("This deploy runs without memory.");
  expect(screen.queryByRole("heading", { name: "Creating your business’s workspace" })).toBeNull();
});

test("the workspace builds itself one app at a time, then the assistant, then offers the way in", async () => {
  vi.useFakeTimers();
  onTestFinished(() => {
    vi.useRealTimers();
  });
  const metrics = { ...AGENT, id: "a2", name: "Metrics", main: false, app: "metrics", icon: "aten" };
  const wiki = { ...AGENT, id: "a3", name: "Wiki", main: false, app: "wiki", icon: "aten" };
  mount(lanes(recorder()), ADMIN, NO_SLACK, [AGENT, metrics, wiki]);
  const settle = () => act(async () => void (await vi.advanceTimersByTimeAsync(0)));
  const tick = () => act(async () => void (await vi.advanceTimersByTimeAsync(BUILD_STEP_MS)));

  await settle();
  fireEvent.click(screen.getByRole("button", { name: "Get started" }));
  await settle();
  fireEvent.click(next());
  fireEvent.change(screen.getByLabelText("About your business"), { target: { value: "Design studio" } });
  fireEvent.click(next());
  fireEvent.click(next());
  fireEvent.click(next());
  fireEvent.click(screen.getByRole("button", { name: "Growing revenue" }));
  fireEvent.click(next());
  await settle();

  expect(screen.getByRole("heading", { name: "Creating your business’s workspace" })).toBeTruthy();
  expect(screen.getByText("Building out foundational apps…")).toBeTruthy();
  expect(screen.getByText("Creating…")).toBeTruthy();
  expect(screen.queryByText("Metrics")).toBeNull();
  await tick();
  expect(screen.getByText("Metrics")).toBeTruthy();
  expect(screen.queryByText("Wiki")).toBeNull();
  await tick();
  expect(screen.getByText("Wiki")).toBeTruthy();
  expect(screen.getByText("Building out foundational apps")).toBeTruthy();
  expect(screen.getByText("Training your assistant…")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Open your workspace" })).toBeNull();
  await tick();
  expect(screen.getByText("assistant")).toBeTruthy();
  expect(screen.getByText("Trained your assistant")).toBeTruthy();
  expect(screen.queryByText("Creating…")).toBeNull();
  await tick();
  fireEvent.click(screen.getByRole("button", { name: "Open your workspace" }));
  expect(location.hash).toBe(HANDED);
});

test("a withheld app is left out of the build screen rather than promised", async () => {
  vi.useFakeTimers();
  onTestFinished(() => {
    vi.useRealTimers();
  });
  const radar = { ...AGENT, id: "a2", name: "Radar", main: false, app: "radar", icon: "aten" };
  const wiki = { ...AGENT, id: "a3", name: "Wiki", main: false, app: "wiki", icon: "aten", hidden: true };
  mount(lanes(recorder()), ADMIN, NO_SLACK, [AGENT, radar, wiki]);
  const settle = () => act(async () => void (await vi.advanceTimersByTimeAsync(0)));
  const tick = () => act(async () => void (await vi.advanceTimersByTimeAsync(BUILD_STEP_MS)));

  await settle();
  fireEvent.click(screen.getByRole("button", { name: "Get started" }));
  await settle();
  fireEvent.click(next());
  fireEvent.change(screen.getByLabelText("About your business"), { target: { value: "Design studio" } });
  fireEvent.click(next());
  fireEvent.click(next());
  fireEvent.click(next());
  fireEvent.click(screen.getByRole("button", { name: "Growing revenue" }));
  fireEvent.click(next());
  await settle();

  expect(screen.queryByText("Wiki")).toBeNull();
  await tick();
  expect(screen.getByText("Radar")).toBeTruthy();
  expect(screen.getByText("Training your assistant…")).toBeTruthy();
  await tick();
  await tick();
  expect(screen.getByRole("button", { name: "Open your workspace" })).toBeTruthy();
  expect(screen.queryByText("Wiki")).toBeNull();
  expect(screen.queryByText("Coming soon")).toBeNull();
});
