import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, onTestFinished, test, vi } from "vitest";

import { App } from "@/App";
import { BUILD_STEP_MS, WATCH_MS, type FirstRunPayload } from "@/views/FirstRun";
import { resetChatStore } from "@/lib/chatStore";
import { HOME_CONNECTORS_LANE, homeConversationLane, homeHash } from "@/lib/route";

import {
  AGENT,
  AGENT_ID,
  CHAT_APP,
  CHAT_APP_ID,
  chatsOnWire,
  CONVO_ID,
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

/** The nine goals the third step offers, in the order it offers them and the order their threads
 *  open in. */
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

/** The goal threads the picks below found, as the run says them. Each one carries the goal's own
 *  instructions: no skill routes on these words, so the words are the whole brief. */
const REVENUE_THREAD =
  "I just set up this workspace. My business: Design studio. My role: Founder. My goal: growing " +
  "revenue. Start by working out my funnel as it stands from whatever CRM, billing, analytics or " +
  "spreadsheet access I have granted: volume at each stage, conversion between them, and average " +
  "deal size. Name the single stage losing the most, and one experiment on it with a metric. Do " +
  "not guess a number I have not given you — say what you could not read. Ask me at most one " +
  "thing.";

const COMPETITORS_THREAD =
  "I just set up this workspace. My business: Design studio. My role: Founder. My goal: " +
  "understanding competitors. Start by naming the competitors my business implies, from my " +
  "website and whatever CRM, support or analytics access I have granted: what each one sells, to " +
  "whom, and at what price. Say where we win and where we lose, in one line each. Do not invent " +
  "a competitor or a price you could not read — say what you could not read. Ask me at most one " +
  "thing.";

const HIRING_THREAD =
  "I just set up this workspace. My business: Design studio. My role: Founder. My goal: hiring. " +
  "Start by listing the roles my business is hiring for, from whatever applicant tracking, " +
  "calendar or email access I have granted, and where each one stands: open, interviewing, or " +
  "offered. Name the one role holding the rest back. Contact no candidate and send nothing. Ask " +
  "me at most one thing.";

/** Other's thread, in the words the member typed for it. */
const OTHER_THREAD =
  "I just set up this workspace. My business: Design studio. My role: Founder. My goal: " +
  "Launching in Japan. Start by saying what you can already read about it from whatever access I " +
  "have granted, and the first step you would take. Do not guess what you could not read — say " +
  "so. Ask me at most one thing.";

/** The business as the default below writes it, said into the chat the run founds off the business
 *  step: the first task is the whole of it, and the run says it before every step that follows. */
const OPENING =
  "I just set up this workspace. My business: Design studio. Set up my first task: a daily competitive analysis.";

/** Where the run leaves the member: the thread it founded, with the connectors screen beside it. */
const HANDED = homeHash({ opens: [homeConversationLane(CONVO_ID), HOME_CONNECTORS_LANE] });

/** The install as the surface object projects it, read by the Slack step on the press. */
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

/** The acts the read projects for this member: the profile collection's confirm is the one the run
 *  draws a step for, and a deploy without the enrichment extension projects none. */
const CONFIRM_VIEW: FirstRunPayload["actions"]["enrichment_profile"][number] = {
  name: "confirm_website",
  description: "Confirm the website the enrichment reads.",
  call: { kind: "enrichment_profile", action: "confirm_website", input: {} },
  input_schema: { properties: { website: { type: "string" } }, required: ["website"] },
  label: "Next",
};

/** The memory collection's own act: the one write the run makes, and the bound it declares on the
 *  body the page composes. */
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

const FIRST_RUN = {
  providers: [
    { name: "slack", label: "Slack", summary: "Send and read messages.", group: "Messaging" },
    { name: "github", label: "GitHub", summary: "Read and write code.", group: "Code" },
  ],
  connectors: [{ name: "slack", label: "Slack", installed: false }],
  ...ACTIONS,
};

/** A deploy running the enrichment extension: the run gains the website step. */
const WITH_WEBSITE = {
  ...FIRST_RUN,
  actions: { ...ACTIONS.actions, enrichment_profile: [CONFIRM_VIEW] },
};

/** What the enrichment made of the confirmed website, as the profile index projects it. */
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

/** The business box as the matched row above writes it: the company summary over its facts. */
const LEARNED = "keep it simple, keep it casual.\nDesign Consulting, 1-10 people, founded 2013.";

/** What the projection holds for a member who cleared the website: the address alone matched
 *  nothing, so there is no company to describe. */
const CLEARED_ROW = {
  ...PROFILE_ROW,
  summary: "Nothing matched.",
  status: "no_match",
  full_name: null,
  job_title: null,
  job_title_role: null,
  job_title_levels: null,
  company_name: null,
  company_industry: null,
  company_size: null,
  company_founded: null,
  company_summary: null,
  company_location: null,
};

const PROFILE_READ: Record<string, Route> = {
  "/objects/enrichment_profile": () => json({ objects: [PROFILE_ROW], next_cursor: null }),
};

const CLEARED_READ: Record<string, Route> = {
  "/objects/enrichment_profile": () => json({ objects: [CLEARED_ROW], next_cursor: null }),
};

/** A deploy offering no Slack install: the run is the three questions. */
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

beforeEach(() => {
  location.hash = "";
  history.replaceState(null, "", location.pathname + "?first=1");
  resetChatStore();
  chat = chatSink();
  acts = recorder();
  useStreamFake();
});

/** The first run's own reads, plus the reads home makes once the run hands the member over, plus
 *  whatever the case wires over them, drawn as far as the welcome. */
function mount(
  routes: Record<string, Route> = {},
  member = ADMIN,
  payload = FIRST_RUN,
  agents = [AGENT],
) {
  const wired = wire({
    ...chatsOnWire([]),
    ...INSTALL_READS,
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

/** The run past its welcome, stood on the first question. */
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

/** Every act the page submitted, in order: the lane below the agent it posted on — `intents` for
 *  an object mutation, the action route for a presented act — and the body it carried. */
function intents(calls: { url: string; body: unknown }[]): unknown[] {
  return calls.map((call) => ({ lane: laneOf(call.url), body: call.body }));
}

function laneOf(url: string): string {
  return url.split("/agents/" + AGENT.id + "/")[1];
}

/** The two lanes an act posts on, answering each with what that act's handler would — an action by
 *  the name its route ends in, any other verb by the verb. Anything unnamed applies, so a case
 *  states only the outcome it is about. */
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

/** Every founding send the run's chat received, as the route receives it: the words, and the url
 *  they were posted to. The run says its opening line by sending it, so the wire is where a case
 *  reads whether it was said — and where a case mid-run reads that nothing was. */
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

/** The acts the run posted, answered as applied. A case reads it for the memory the run wrote, and
 *  wires its own lane where it is about a refusal. */
let acts = recorder();

/** The run's end: the workspace building itself, then Open your workspace onto home, standing the
 *  thread the run founded with the connectors screen beside it.
 *
 *  The opening line is the first thing said, not the only one: it left on the business step, and a
 *  run that picked goals founded a thread per goal after it. */
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

/** Where the head says the run stands: the step it is on, and how many it counts. */
function counted(): (string | null)[] {
  const progress = screen.getByRole("progressbar", { name: "Step" });
  return [progress.getAttribute("aria-valuenow"), progress.getAttribute("aria-valuemax")];
}

/** Answers the first question and moves on. */
async function describeBusiness(about = "Design studio") {
  await userEvent.type(await screen.findByLabelText("About your business"), about);
  await userEvent.click(next());
}

/** Answers the second question and moves on. */
async function pickRole(role = "Founder") {
  await userEvent.click(await screen.findByRole("radio", { name: role }));
  await userEvent.click(next());
}

/** Answers, or passes, the third question and moves on. Every named goal is picked on the way, and
 *  Other's words are typed where they are given. */
async function pickGoals(goals: string[] = [], other = "") {
  await screen.findByRole("heading", { name: "What is top of mind right now?" });
  for (const goal of goals) await userEvent.click(screen.getByRole("button", { name: goal }));
  if (other) await userEvent.type(screen.getByLabelText("What is top of mind"), other);
  await userEvent.click(next());
}

/** The three questions answered with the defaults, stood on the step after them. */
async function answer() {
  await describeBusiness();
  await pickRole();
  await pickGoals();
}

/** The member coming back from the provider's install page: the tab they left is looked at again,
 *  which is the whole account this page has of an install granted somewhere else. */
async function returning() {
  for (const state of ["hidden", "visible"]) {
    Object.defineProperty(document, "visibilityState", { value: state, configurable: true });
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
  }
}

test("the run opens on the welcome, and Get started leads to the first question", async () => {
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
  expect(screen.queryByLabelText("About your business")).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Get started" }));

  await screen.findByRole("heading", { name: "Tell us a bit about your business" });
  expect(screen.queryByRole("button", { name: "Get started" })).toBeNull();
});

test("closing the welcome opens the chat", async () => {
  mount();

  await userEvent.click(await screen.findByRole("button", { name: "Close" }));

  await screen.findByPlaceholderText("Start new chat…");
  expect(location.hash).not.toBe("#/first-run");
});

/** Closing walks out of the run rather than finishing it: nothing more is said, and the first
 *  task's own thread — founded back on the business step — is left running. */
test("closing a step opens the chat with nothing more asked", async () => {
  await open(lanes(recorder()));

  await answer();
  await screen.findByRole("heading", { name: "Connect your messaging app" });
  await userEvent.click(screen.getByRole("button", { name: "Close" }));

  await screen.findByPlaceholderText("Start new chat…");
  expect(location.hash).not.toBe("#/first-run");
  expect(chat.sent).toEqual([OPENING]);
});

test("the card's query lands on the first run's own address", async () => {
  await open();

  await waitFor(() => expect(location.hash).toBe("#/first-run"));
  await screen.findByRole("heading", { name: "Tell us a bit about your business" });
  expect(screen.queryByPlaceholderText("Start new chat…")).toBeNull();
});

test("the address opens the first run on its own, with no query at all", async () => {
  history.replaceState(null, "", location.pathname);
  location.hash = "#/first-run";
  await open();

  await screen.findByLabelText("About your business");
  expect(location.hash).toBe("#/first-run");
});

test("the page draws no shell around the step", async () => {
  await open();

  await screen.findByRole("heading", { name: "Tell us a bit about your business" });
  expect(screen.queryByRole("banner")).toBeNull();
  expect(screen.queryByRole("button", { name: "Menu" })).toBeNull();
});

test("the head counts four steps with Slack, advancing one per answer under a Close on each", async () => {
  await open(lanes(recorder()));

  await screen.findByLabelText("About your business");
  expect(counted()).toEqual(["1", "4"]);
  expect(screen.getByRole("progressbar", { name: "Step" }).childElementCount).toBe(4);
  expect(screen.getByRole("button", { name: "Close" })).toBeTruthy();

  await describeBusiness();
  await screen.findByRole("heading", { name: "What is your role at the business?" });
  expect(counted()).toEqual(["2", "4"]);
  expect(screen.getByRole("button", { name: "Close" })).toBeTruthy();

  await pickRole();
  await screen.findByRole("heading", { name: "What is top of mind right now?" });
  expect(counted()).toEqual(["3", "4"]);
  expect(screen.getByRole("button", { name: "Close" })).toBeTruthy();

  await pickGoals();
  await screen.findByRole("heading", { name: "Connect your messaging app" });
  expect(counted()).toEqual(["4", "4"]);
  expect(screen.getByRole("button", { name: "Close" })).toBeTruthy();
});

test("a deploy without Slack counts three steps, and the third's Next finishes the run", async () => {
  await open(lanes(recorder()), ADMIN, NO_SLACK);

  await screen.findByLabelText("About your business");
  expect(counted()).toEqual(["1", "3"]);
  expect(screen.getByRole("progressbar", { name: "Step" }).childElementCount).toBe(3);

  await describeBusiness();
  await pickRole();
  await screen.findByRole("heading", { name: "What is top of mind right now?" });
  expect(counted()).toEqual(["3", "3"]);
  await userEvent.click(next());

  await built();
  expect(screen.queryByRole("heading", { name: "Connect your messaging app" })).toBeNull();
});

test("Cmd+Enter is Next on every question, and holds where Next is disabled", async () => {
  await open();

  const about = await screen.findByLabelText("About your business");
  await userEvent.keyboard("{Meta>}{Enter}{/Meta}");
  expect(screen.queryByRole("heading", { name: "What is your role at the business?" })).toBeNull();

  await userEvent.type(about, "Design studio");
  await userEvent.keyboard("{Meta>}{Enter}{/Meta}");
  await screen.findByRole("heading", { name: "What is your role at the business?" });

  await userEvent.keyboard("{Control>}{Enter}{/Control}");
  await screen.findByRole("heading", { name: "What is top of mind right now?" });

  await userEvent.click(screen.getByRole("button", { name: "Other" }));
  await userEvent.keyboard("{Meta>}{Enter}{/Meta}");
  expect(screen.getByRole("heading", { name: "What is top of mind right now?" })).toBeTruthy();
});

test("the first step gates Next on the business and names who is answering", async () => {
  await open();

  const about = await screen.findByLabelText("About your business");
  expect(about).toBeInstanceOf(HTMLTextAreaElement);
  expect(about.getAttribute("placeholder")).toBe(
    "Software startup, Marketing agency, Design studio, AI consulting…",
  );
  expect(screen.getByText(ADMIN.email)).toBeTruthy();
  expect(next().disabled).toBe(true);

  await userEvent.type(about, "  ");
  expect(next().disabled).toBe(true);
  await userEvent.type(about, "Design studio");
  expect(next().disabled).toBe(false);

  await userEvent.click(next());
  await screen.findByRole("heading", { name: "What is your role at the business?" });
});

test("the role step offers the twelve roles, checks Founder until told otherwise, and Other asks for words", async () => {
  await open();
  await describeBusiness();

  const roles = within(await screen.findByRole("radiogroup", { name: "Role" })).getAllByRole("radio");
  expect(roles.map((role) => role.textContent)).toEqual(ROLES);
  expect(screen.getByRole("radio", { checked: true }).textContent).toBe("Founder");
  expect(next().disabled).toBe(false);
  expect(screen.queryByLabelText("Your role")).toBeNull();

  await userEvent.click(screen.getByRole("radio", { name: "Sales" }));
  expect(screen.getByRole("radio", { checked: true }).textContent).toBe("Sales");

  await userEvent.click(screen.getByRole("radio", { name: "Other" }));
  const own = screen.getByLabelText("Your role");
  expect(document.activeElement).toBe(own);
  expect(next().disabled).toBe(true);
  await userEvent.type(own, "Barista");
  expect(next().disabled).toBe(false);
});

/** The role's own tools, offered as a multiple choice: the engineer is asked about the code and
 *  issue tools, and the picks are what the step then connects. A tool this deploy's catalog does
 *  not carry is not offered, so the grid names only what a press can grant. */
test("the tools step offers the connectors the picked role works in", async () => {
  await open(lanes(recorder()));
  await describeBusiness();
  await pickRole("Engineer");

  await screen.findByRole("heading", { name: "Which tools do you work in?" });
  expect(counted()).toEqual(["3", "5"]);
  const tools = within(screen.getByRole("group", { name: "Tools" })).getAllByRole("button");
  expect(tools.map((tool) => tool.textContent)).toEqual(["GitHub"]);
  expect(tools.map((tool) => tool.getAttribute("aria-pressed"))).toEqual(["false"]);
  expect(next().disabled).toBe(false);
});

/** A role whose tools this deploy does not carry has no step: the run counts four and goes from the
 *  role straight to the goals. */
test("a role whose tools the catalog does not carry stands no tools step", async () => {
  await open(lanes(recorder()));
  await describeBusiness();
  await pickRole("Sales");

  await screen.findByRole("heading", { name: "What is top of mind right now?" });
  expect(counted()).toEqual(["3", "4"]);
});

/** Picking nothing is skipping: Next carries straight on to the goals, and nothing is connected. */
test("the tools step skips to the goals where nothing is picked", async () => {
  const posted = recorder();
  await open(lanes(posted));
  await describeBusiness();
  await pickRole("Engineer");

  await screen.findByRole("heading", { name: "Which tools do you work in?" });
  await userEvent.click(next());

  await screen.findByRole("heading", { name: "What is top of mind right now?" });
  expect(intents(posted.calls)).toEqual([]);
});

/** The picks are connected on the step itself: each one presses the act its own provider takes —
 *  the broker's connect verb for an account that is the member's. */
test("a picked tool is stood to connect, and the press asks for that account", async () => {
  const posted = recorder();
  await open(lanes(posted));
  const consent = { focus: vi.fn(), close: vi.fn(), location: { href: "" } };
  const opened = vi.spyOn(window, "open").mockReturnValue(consent as unknown as Window);
  onTestFinished(() => opened.mockRestore());

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

/** Next off the connect list carries on with whatever is left unconnected: the run asks for the
 *  accounts once and never holds the member on them. */
test("Next off the connect list carries on to the goals", async () => {
  await open(lanes(recorder()));
  await describeBusiness();
  await pickRole("Engineer");
  await screen.findByRole("heading", { name: "Which tools do you work in?" });
  await userEvent.click(screen.getByRole("button", { name: "GitHub" }));
  await userEvent.click(next());

  await screen.findByRole("heading", { name: "Connect the tools you picked" });
  await userEvent.click(next());

  await screen.findByRole("heading", { name: "What is top of mind right now?" });
});

test("the top-of-mind step offers the nine goals with none pressed, and Other asks for words", async () => {
  await open();
  await describeBusiness();
  await pickRole();

  await screen.findByRole("heading", { name: "What is top of mind right now?" });
  const goals = within(screen.getByRole("group", { name: "Top of mind" })).getAllByRole("button");
  expect(goals.map((goal) => goal.textContent)).toEqual(GOALS);
  expect(goals.map((goal) => goal.getAttribute("aria-pressed"))).toEqual(GOALS.map(() => "false"));
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
  await describeBusiness();
  await pickRole("Engineer");
  // The engineer's own tools stand between the role and the goals.
  await screen.findByRole("heading", { name: "Which tools do you work in?" });
  await userEvent.click(next());
  await screen.findByRole("heading", { name: "What is top of mind right now?" });
  await userEvent.click(screen.getByRole("button", { name: "Hiring" }));

  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  await screen.findByRole("heading", { name: "Which tools do you work in?" });
  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  expect((await screen.findByRole("radio", { checked: true })).textContent).toBe("Engineer");
  expect(counted()).toEqual(["2", "5"]);

  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  expect(((await screen.findByLabelText("About your business")) as HTMLTextAreaElement).value).toBe(
    "Design studio",
  );
  expect(counted()).toEqual(["1", "5"]);

  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  await screen.findByRole("heading", { name: "An AI operating system for your business" });
  await userEvent.click(screen.getByRole("button", { name: "Get started" }));
  expect(((await screen.findByLabelText("About your business")) as HTMLTextAreaElement).value).toBe(
    "Design studio",
  );

  await userEvent.click(next());
  await screen.findByRole("radiogroup", { name: "Role" });
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
  expect(screen.getByRole("button", { name: "I use something different" })).toBeTruthy();
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

/** The link is minted on the intent lane and never through the chat; a browser that refuses the
 *  window is the one case the step still renders it. */
test("a browser that refuses the window still hands the member the link", async () => {
  const posted = recorder({ slack_connect: { applied: true, message: "", url: SLACK_LINK } });
  await open(lanes(posted));

  await answer();
  await userEvent.click(await screen.findByRole("button", { name: "Connect Slack" }));

  const link = await screen.findByRole("link", { name: "Open the Slack install page" });
  expect(link.getAttribute("href")).toBe(SLACK_LINK);
  expect(intents(posted.calls)).toEqual([{ lane: "actions/surface/slack/slack_connect", body: {} }]);
  expect(location.hash).toBe("#/first-run");
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

test("a member who is not an admin is told who installs, and finishes past it", async () => {
  await open(lanes(recorder()), MEMBER);

  await answer();

  await screen.findByText("A workspace admin connects Slack.");
  expect(screen.queryByRole("button", { name: "Connect Slack" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "I use something different" }));
  await built();
});

test("a connector the workspace already holds opens on the success screen, and Continue finishes", async () => {
  await open(lanes(recorder()), ADMIN, HELD_SLACK);

  await answer();

  await screen.findByRole("heading", { name: "We were able to connect to Slack" });
  expect(screen.getByText("This installed UFO in Slack for your team")).toBeTruthy();
  expect(screen.getByText("Success")).toBeTruthy();
  expect(counted()).toEqual(["4", "4"]);
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

/** The install is granted in another window and this tab never goes away, so the step's own watch
 *  is the only read that sees it: the page's own read is half a minute behind. */
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
  fireEvent.change(screen.getByLabelText("About your business"), {
    target: { value: "Design studio" },
  });
  fireEvent.click(next());
  fireEvent.click(screen.getByRole("radio", { name: "Founder" }));
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
  expect(chat.sent).toEqual([OPENING]);
});

test("I use something different finishes the run with the answers written, as one sentence", async () => {
  const posted = recorder();
  await open(lanes(posted));

  await describeBusiness("A two-person design studio.");
  await pickRole();
  await pickGoals();
  await userEvent.click(await screen.findByRole("button", { name: "I use something different" }));

  await built(
    "I just set up this workspace. My business: A two-person design studio. Set up my first task: a daily competitive analysis.",
  );
  expect(intents(posted.calls)).toEqual([
    {
      lane: "actions/memory/record_first_run",
      body: { body: "A two-person design studio. Their role: Founder." },
    },
  ]);
});

/** The run ends on home, standing the thread it founded for the first task, and the connectors
 *  screen stands beside it: the member reads the work already running and connects the tools it
 *  needs without leaving the page. */
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
    ).toEqual(["Conversation", "Connectors"]),
  );
});

/** The chat app home stands is the agent the first task is said to, whichever agent the run itself
 *  was drawn for. */
test("the opening line is said to the chat app home stands, not the agent the run was drawn for", async () => {
  await open(lanes(recorder()), ADMIN, NO_SLACK, [AGENT, CHAT_APP]);

  await describeBusiness();

  await waitFor(() => expect(chat.sent).toEqual([OPENING]));
  expect(chat.posted[0]).toContain("/agents/" + CHAT_APP_ID + "/chat?conversation=new");
});

/** Coming through the run a second time is a second opening line, so it opens a conversation of its
 *  own: the lane the member is handed cannot be the one the last run founded. */
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
    await screen.findByRole("button", { name: "Open your workspace" }, { timeout: BUILD_STEP_MS * 4 }),
  );

  await waitFor(() => expect(chat.sent).toEqual([OPENING, OPENING]));
  expect(chat.posted).toEqual([chat.posted[0], chat.posted[0]]);
  expect(chat.posted[1]).toContain("conversation=new");
});

/** The store signs one file and refuses the other, so the send names the key it took and carries
 *  the file it did not — both beside the words. */
test("the website step opens the run where the deploy offers the act, counts five steps with Slack, and is prefilled with the address's domain", async () => {
  await open({}, ADMIN, WITH_WEBSITE);

  await screen.findByRole("heading", { name: "Confirm your website" });
  const website = screen.getByLabelText("Website") as HTMLInputElement;
  expect(website.value).toBe("example.com");
  expect(counted()).toEqual(["1", "5"]);
  expect(screen.queryByLabelText("About your business")).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  await screen.findByRole("heading", { name: "An AI operating system for your business" });
  expect(screen.queryByRole("progressbar", { name: "Step" })).toBeNull();
});

test("a mail provider's domain is not prefilled as the website", async () => {
  await open({}, { ...ADMIN, email: "member@gmail.com" }, WITH_WEBSITE);

  await screen.findByRole("heading", { name: "Confirm your website" });
  expect((screen.getByLabelText("Website") as HTMLInputElement).value).toBe("");
});

test("clearing the website empties the field, Next posts an empty website, and the business box is left empty", async () => {
  const posted = recorder();
  await open({ ...lanes(posted), ...CLEARED_READ }, ADMIN, WITH_WEBSITE);

  await screen.findByRole("heading", { name: "Confirm your website" });
  const website = screen.getByLabelText("Website") as HTMLInputElement;

  await userEvent.click(screen.getByRole("button", { name: "Clear" }));
  expect(website.value).toBe("");
  expect(screen.queryByRole("button", { name: "Clear" })).toBeNull();
  await userEvent.click(next());

  await screen.findByRole("heading", { name: "Tell us a bit about your business" });
  expect(((await screen.findByLabelText("About your business")) as HTMLTextAreaElement).value).toBe(
    "",
  );
  expect(counted()).toEqual(["2", "5"]);
  expect(intents(posted.calls)).toEqual([
    { lane: "actions/enrichment_profile/confirm_website", body: { website: "" } },
  ]);
});

test("a matched website writes the business box, and the member's own words hold", async () => {
  await open({ ...lanes(recorder()), ...PROFILE_READ }, ADMIN, WITH_WEBSITE);

  await screen.findByRole("heading", { name: "Confirm your website" });
  await userEvent.click(next());

  const about = (await screen.findByLabelText("About your business")) as HTMLTextAreaElement;
  expect(about.value).toBe(LEARNED);
  expect(next().disabled).toBe(false);

  await userEvent.clear(about);
  await userEvent.type(about, "A two-person design studio");
  expect(about.value).toBe("A two-person design studio");
});

test("confirming posts the website, pre-selects Founder, and the top-of-mind step names the company", async () => {
  const posted = recorder();
  await open({ ...lanes(posted), ...PROFILE_READ }, ADMIN, WITH_WEBSITE);

  await screen.findByRole("heading", { name: "Confirm your website" });
  await userEvent.click(next());
  await describeBusiness();

  const founder = await screen.findByRole("radio", { name: "Founder" });
  expect(founder.getAttribute("aria-checked")).toBe("true");
  expect(next().disabled).toBe(false);
  expect(intents(posted.calls)).toEqual([
    { lane: "actions/enrichment_profile/confirm_website", body: { website: "example.com" } },
  ]);
  await userEvent.click(next());

  await screen.findByRole("heading", { name: "What is top of mind right now?" });
  expect(screen.getByText("Simplecasual")).toBeTruthy();
});

test("a refused confirmation is stated as a toast and holds the website step", async () => {
  const posted = recorder({ confirm_website: { applied: false, message: "No key." } });
  await open({ ...lanes(posted), ...PROFILE_READ }, ADMIN, WITH_WEBSITE);

  await screen.findByRole("heading", { name: "Confirm your website" });
  await userEvent.click(next());

  await screen.findByText("No key.");
  expect(screen.getByRole("heading", { name: "Confirm your website" })).toBeTruthy();
  expect(screen.queryByRole("radio")).toBeNull();
});

test("each question opens with its own input focused", async () => {
  await open({ ...lanes(recorder()), ...PROFILE_READ }, ADMIN, WITH_WEBSITE);

  expect(document.activeElement).toBe(await screen.findByLabelText("Website"));
  await userEvent.click(next());
  expect(document.activeElement).toBe(await screen.findByLabelText("About your business"));
  await describeBusiness();
  expect(document.activeElement).toBe(await screen.findByRole("radio", { name: "Founder" }));
  await userEvent.click(next());
  await userEvent.click(await screen.findByRole("button", { name: "Other" }));
  expect(document.activeElement).toBe(screen.getByLabelText("What is top of mind"));
});

test("a goal is picked and unpicked", async () => {
  await open();
  await describeBusiness();
  await pickRole();

  await screen.findByRole("heading", { name: "What is top of mind right now?" });
  const revenue = screen.getByRole("button", { name: "Growing revenue" });
  expect(revenue.getAttribute("aria-pressed")).toBe("false");
  await userEvent.click(revenue);
  expect(revenue.getAttribute("aria-pressed")).toBe("true");
  await userEvent.click(revenue);
  expect(revenue.getAttribute("aria-pressed")).toBe("false");
});

test("the run writes the business, the role and the goals to memory before it hands over", async () => {
  await open({}, ADMIN, NO_SLACK);
  await describeBusiness();
  await pickRole();
  await pickGoals(["Growing revenue", "Fundraising"]);
  await built();

  expect(intents(acts.calls)).toEqual([
    {
      lane: "actions/memory/record_first_run",
      body: { body: "Design studio. Their role: Founder. Their goals: growing revenue, fundraising." },
    },
  ]);
});

test("Other's words are written to memory as the member typed them, and open a thread of their own", async () => {
  await open({}, ADMIN, NO_SLACK);
  await describeBusiness();
  await pickRole();
  await pickGoals(["Hiring", "Other"], "Launching in Japan.");

  await screen.findByRole("heading", { name: "Creating your business’s workspace" });
  expect(chat.sent).toEqual([OPENING, HIRING_THREAD, OTHER_THREAD]);
  await built();
  expect(intents(acts.calls)).toEqual([
    {
      lane: "actions/memory/record_first_run",
      body: { body: "Design studio. Their role: Founder. Their goals: hiring, Launching in Japan." },
    },
  ]);
});

test("a run with no goal picked still writes who the workspace is for", async () => {
  await open({}, ADMIN, NO_SLACK);
  await answer();
  await built();

  expect(intents(acts.calls)).toEqual([
    { lane: "actions/memory/record_first_run", body: { body: "Design studio. Their role: Founder." } },
  ]);
});

test("each picked goal opens a thread of its own, before the workspace screen", async () => {
  await open({}, ADMIN, NO_SLACK);
  await describeBusiness();
  await pickRole();
  await pickGoals(["Growing revenue", "Understanding competitors"]);

  await screen.findByRole("heading", { name: "Creating your business’s workspace" });
  expect(chat.sent).toEqual([OPENING, REVENUE_THREAD, COMPETITORS_THREAD]);
  for (const url of chat.posted) {
    expect(url).toContain("/agents/" + AGENT_ID + "/chat?conversation=new");
  }
});

test("the workspace screen names the goals it opened a thread on", async () => {
  await open({}, ADMIN, NO_SLACK);
  await describeBusiness();
  await pickRole();
  await pickGoals(["Growing revenue"]);

  await screen.findByRole("heading", { name: "Creating your business’s workspace" });
  await screen.findByText("Started work on your goals", {}, { timeout: BUILD_STEP_MS * 6 });
  expect(screen.getByText("Growing revenue")).toBeTruthy();
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
  fireEvent.change(screen.getByLabelText("About your business"), { target: { value: "Design studio" } });
  fireEvent.click(next());
  fireEvent.click(next());
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

/** An app this deploy withholds is not drawn at all. `hidden` says the portal withholds it from
 *  every list it draws, never that the app is unbuilt — so naming it here would state something
 *  untrue about a shipped app the workspace already holds. */
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
  fireEvent.change(screen.getByLabelText("About your business"), { target: { value: "Design studio" } });
  fireEvent.click(next());
  fireEvent.click(next());
  fireEvent.click(next());
  await settle();

  expect(screen.queryByText("Wiki")).toBeNull();
  await tick();
  expect(screen.getByText("Radar")).toBeTruthy();
  expect(screen.getByText("Training your assistant…")).toBeTruthy();
  await tick();
  await tick();
  expect(screen.getByRole("button", { name: "Open your workspace" })).toBeTruthy();
  // The withheld app is named nowhere on the screen, and nothing promises it.
  expect(screen.queryByText("Wiki")).toBeNull();
  expect(screen.queryByText("Coming soon")).toBeNull();
});
