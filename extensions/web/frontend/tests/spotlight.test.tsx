import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { homeConversationLane, homeHash, mintHomeLane, parseHash } from "@/lib/route";

import { AGENT, AGENT_ID, atPhoneWidth, CHAT_ROW, chatsOnWire, CONVO_ID, json, MEMBER, objectIndex, owned, type Route, SECOND, SECOND_ID, SITE_KIND, StreamFake, TASK_KIND, TRIGGER_KIND, TURN_ID, useStreamFake, wire } from "./harness";

/** The bar the palette is opened from stands on the phone bar and in the workspace column the
 *  drawer holds, so the width these are read at is the one that draws it. */
beforeEach(() => {
  location.hash = "";
  atPhoneWidth();
  useStreamFake();
});

/** The desk shell, where the rail stands beside the pane and the launcher is a tile on it. */
function atDeskWidth() {
  vi.stubGlobal("matchMedia", (media: string) => ({
    media,
    matches: false,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }));
}

const FOUNDED_ID = "77777777-7777-4777-8777-777777777777";
const SHARED_ID = "66666666-6666-4666-8666-666666666666";
const SLACK_ID = "99999999-9999-4999-8999-999999999999";
const OWN_ID = "2b3c4d5e-6f70-4819-8a2b-3c4d5e6f7081";
const OTHER_ID = "1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f";
const GRANT = "0d1f2e3a-4b5c-4d6e-8f70-112233445566";

const FOUND_CONVERSATION = {
  id: CONVO_ID,
  agent: { id: AGENT_ID, name: "assistant" },
  surface: "web",
  surface_label: null,
  audience: "shared",
  member_email: null,
  description: "Rename the deploy job",
  source: null,
  speakers: [],
  turn_count: 2,
  created_at: "2026-07-30T10:00:00",
  last_turn_at: "2026-07-30T11:00:00",
  readable: true,
  disclosable: false,
  commentable: false,
};

/** Two found conversations the rail does not carry: one the member owns, one another member does.
 *  Whose each is is the read's own answer, and it is all the filter has to go on. */
const FOUND_OWN_CONVERSATION = {
  ...FOUND_CONVERSATION,
  id: OWN_ID,
  audience: "member:" + MEMBER.id,
  member_email: MEMBER.email,
  description: "Roll the release back",
};

const FOUND_THEIR_CONVERSATION = {
  ...FOUND_CONVERSATION,
  id: OTHER_ID,
  audience: "member:other",
  member_email: "other@example.com",
  description: "Rotate the signing key",
};

const FOUND_FILE = {
  name: "conv1-deploy-plan-md",
  filename: "deploy-plan.md",
  subject: null,
  media_type: "text/markdown",
  size_bytes: 64,
  created_at: "2026-08-14T09:00:00",
  url: "/dl/deploy-plan.md",
  preview_url: null,
  owner_email: MEMBER.email,
};


const FOUND_TASK = owned({
  name: "nightly-deploy",
  summary: "0 2 * * * — deploy",
  conversation: CONVO_ID,
  mine: true,
  next_run_at: "2026-08-19T02:00:00Z",
  last_run_at: null,
  last_run_status: null,
  paused: false,
});

/** The record a radar hit opens, wired ahead of the index it was found in — the stub matches on
 *  the path it is given, and the index's own path is a prefix of the record's. */
const FOUND_TASK_DETAIL = {
  ...TASK_KIND,
  name: "nightly-deploy",
  summary: "0 2 * * * — deploy",
  spec: { schedule: "0 2 * * *", prompt: "deploy", paused: false },
  status: { next_run_at: "2026-08-19T02:00:00Z", paused: false, owner_email: MEMBER.email },
  links: [],
  created_at: "2026-08-01T09:00:00Z",
  updated_at: "2026-08-01T09:00:00Z",
};

/** The reads every screen behind the palette makes, each answering with nothing, so a suite states
 *  only the kind it is about. */
const QUIET = {
  "/slots": () => json({ slots: [] }),
  "/objects/artifact": () => json({ objects: [] }),
  "/workspace/memory": () => json({ available: true, kinds: [], matches: [] }),
  ["/objects/" + TASK_KIND.kind]: () => objectIndex(TASK_KIND, []),
  ["/objects/" + TRIGGER_KIND.kind]: () => objectIndex(TRIGGER_KIND, []),
  ["/objects/" + SITE_KIND.kind]: () => objectIndex(SITE_KIND, []),
  "/transcript": () => json({ messages: [] }),
};

/** Every read the spotlight fans out to, each answering the one term. */
function everything(extra: Record<string, Route> = {}) {
  return wire({
    ["/objects/" + TASK_KIND.kind + "/nightly-deploy"]: () => json(FOUND_TASK_DETAIL),
    ...QUIET,
    "/conversations$": () => json({ conversations: [FOUND_CONVERSATION] }),
    "/objects/artifact": () => json({ objects: [FOUND_FILE] }),
    ["/objects/" + TASK_KIND.kind]: () => objectIndex(TASK_KIND, [FOUND_TASK]),
    ...extra,
  });
}

const REFUSED = () => new Response("nope", { status: 500 });

function nothing() {
  return wire(QUIET);
}

/** A conversation spoken in minutes ago, which is what stands its app under `Active`. */
const RECENT = new Date(Date.now() - 5 * 60_000).toISOString();

const MINE_CHAT = { ...CHAT_ROW, last_at: RECENT };

const SHARED_CHAT = {
  ...CHAT_ROW,
  conversation_id: SHARED_ID,
  title: "Rollout notes",
  mine: false,
};

const SLACK_CHAT = {
  ...CHAT_ROW,
  conversation_id: SLACK_ID,
  agent_id: SECOND_ID,
  agent_name: "second",
  title: "Standup in ops",
  surface: "slack",
};

/** The member's conversations as the rail carries them: two the assistant holds, one the second app
 *  held on Slack. */
const RAIL = [MINE_CHAT, SHARED_CHAT, SLACK_CHAT];

function railed() {
  return wire({ ...chatsOnWire(RAIL), ...QUIET });
}

/** The shipped app whose pane holds the workspace's files: a file hit lands on the app that reads
 *  its kind. A task hit lands on the workspace's own tasks tab, which needs no app. */
const ARTIFACTS_PURPOSE = "Holds the files and sites the workspace makes.";

const ARTIFACTS_APP = {
  id: "7f1b9f6e-9f30-4f8f-9a6e-1d9d1c2b3a41",
  name: "artifacts",
  model: "auto",
  main: false,
  icon: "stele",
  app: "artifacts",
  purpose: ARTIFACTS_PURPOSE,
};

function portal() {
  render(
    <App agents={[AGENT, SECOND, ARTIFACTS_APP]} member={MEMBER} onAgents={() => {}} />,
  );
}

function open() {
  portal();
  return userEvent.click(screen.getByRole("button", { name: "Search" }));
}

async function type(term: string) {
  const box = await screen.findByRole("combobox", { name: "Search" });
  await userEvent.type(box, term);
  return box;
}

/** The heading over each run of rows. cmdk hides the heading element itself and points the group's
 *  label at it, so the order they stand in is read off the elements rather than off a role. */
function headings() {
  return [...document.querySelectorAll("[cmdk-group-heading]")].map((head) => head.textContent);
}

/** The rows one run holds, in the order they stand. cmdk names a group by the heading over it, so a
 *  run is reached by the words a member reads it under. */
function rowsUnder(heading: string): string[] {
  const run = document.querySelector('[cmdk-group][data-value="' + heading + '"]');
  if (!run) throw new Error("the palette draws no " + heading + " run");
  return [...run.querySelectorAll("[cmdk-item]")].map((row) => String(row.textContent));
}

/** The lanes home's address states, in the order they stand. A row taken from the palette lands
 *  here, so the lane it opened is read off the address the press wrote. */
function standing(): string[] {
  const route = parseHash(location.hash);
  if (route?.kind !== "home") throw new Error("the palette did not land home: " + location.hash);
  return route.place.opens ?? [];
}

/** The bar under the rows: what the panel is showing, then the keys that act on it as it stands. */
function foot(): string {
  const bar = document.querySelector("[data-slot=command-foot]");
  if (!bar) throw new Error("the palette draws no foot");
  return String(bar.textContent);
}

/** Narrowing the box to one app or one surface: the chord walks to the scopes, and the row naming
 *  the scope stands the box inside it. */
async function intoScope(label: string) {
  await userEvent.keyboard("{Meta>}k{/Meta}");
  await userEvent.click(await screen.findByRole("option", { name: scopeRow(label) }));
}

/** A scope row names the search it stands for and states the kind it narrows to, so the row is
 *  matched on the search rather than on the whole line. */
function scopeRow(label: string): RegExp {
  return new RegExp("^Search " + label + " threads");
}

/** The pool refuses here, and the palette says nothing about it: a connector read the member cannot
 *  act on from the box states a line under every term they type, so a refusal reads as no hits and
 *  the group falls away with them. */
test("one term reaches every kind the workspace holds, each hit under its own heading", async () => {
  const { calls } = everything({ "/connections": REFUSED });
  await open();
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  expect(await found.findByRole("option", { name: /Rename the deploy job/ })).toBeTruthy();
  expect(found.getByRole("option", { name: /deploy-plan.md/ })).toBeTruthy();
  expect(found.getByRole("option", { name: /nightly-deploy/ })).toBeTruthy();
  expect(headings()).toEqual(["Actions", "Threads", "Artifacts", "Tasks"]);
  expect(found.queryByText(/Error 500/)).toBeNull();

  const asked = calls.filter((url) => url.includes("q=deploy"));
  expect(asked.some((url) => url.includes("/objects/artifact"))).toBe(true);
  expect(asked.some((url) => url.includes("/agents/" + AGENT_ID + "/conversations"))).toBe(true);
  expect(asked.some((url) => url.includes("/agents/" + SECOND_ID + "/conversations"))).toBe(true);
  expect(asked.some((url) => url.includes("/objects/" + TASK_KIND.kind))).toBe(true);
});

/** An agent is named by the boot payload the shell already holds, so it needs no read of its own.
 *  The term reaches it as the app itself and as nothing else: the search one scope stands for is
 *  reached by the chord, not by a row standing among the term's own answers. */
test("an agent matches from the payload the shell holds, under its own heading", async () => {
  const { calls } = everything();
  await open();
  await type("second");

  const found = within(await screen.findByRole("dialog"));
  expect(await found.findByRole("option", { name: "Second" })).toBeTruthy();
  expect(found.queryByRole("option", { name: scopeRow("Second") })).toBeNull();
  expect(headings().slice(0, 2)).toEqual(["Actions", "Applications"]);
  expect(calls.some((url) => url.includes("/api/agents?q="))).toBe(false);
});

/** Memory is gone from the workspace, so the box does not read it: a kind the deploy no longer
 *  holds would stand a group under every term that the member cannot open. */
test("the box reads no memory and stands no memory group", async () => {
  const { calls } = everything();
  await open();
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  expect(await found.findByRole("option", { name: /Rename the deploy job/ })).toBeTruthy();
  expect(headings()).not.toContain("Memory");
  expect(calls.some((url) => url.includes("/workspace/memory"))).toBe(false);
});

test("a hit opens the place that holds it", async () => {
  everything();
  await open();
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  await userEvent.click(await found.findByRole("option", { name: /Rename the deploy job/ }));

  expect(standing()[0]).toBe(homeConversationLane(CONVO_ID));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});


/** A site belongs to the workspace, not to an agent, so its read names one agent — as the
 *  artifacts screen's does. Fanning it out would list the one site once per agent. It is listed
 *  and opened where sites live, which is the artifacts screen, never the radar feed. */

/** An object's name is unique under its own agent, not across the workspace, so two agents may
 *  each hold a `nightly-deploy`. Both stand, and each hit opens the lane naming its own agent: the
 *  search cannot contradict the index it reads. */
test("two agents' same-named records both stand, each opening its own", async () => {
  const second = owned({ ...FOUND_TASK, mine: false }, SECOND);
  wire({
    ["/objects/" + TASK_KIND.kind + "/nightly-deploy"]: () => json(FOUND_TASK_DETAIL),
    ...QUIET,
    ["/objects/" + TASK_KIND.kind]: () => objectIndex(TASK_KIND, [FOUND_TASK, second]),
  });
  await open();
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  const rows = await found.findAllByRole("option", { name: /nightly-deploy/ });
  expect(rows).toHaveLength(2);

  await userEvent.click(rows[1]);
  expect(location.hash.startsWith("#/workspace/tasks")).toBe(true);
  expect(decodeURIComponent(location.hash)).toContain(
    "object/" + SECOND_ID + "/" + TASK_KIND.kind + "/nightly-deploy",
  );
});


/** A read that refused is not a kind with no hits: the group states the refusal, so the member
 *  never reads a searched workspace as an empty one. */
test("a read that fails states so under its own heading, and the others still answer", async () => {
  wire({
    ...QUIET,
    "/conversations$": () => json({ conversations: [FOUND_CONVERSATION] }),
    "/objects/artifact": () => new Response("nope", { status: 503 }),
  });
  await open();
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  expect(await found.findByText("Error 503 — reload to retry.")).toBeTruthy();
  expect(found.getByRole("option", { name: /Rename the deploy job/ })).toBeTruthy();
});

test("a term nothing answers says so once, not once per kind", async () => {
  nothing();
  await open();
  await type("nothing matches this");

  const found = within(await screen.findByRole("dialog"));
  expect(await found.findByText("Nothing matches this search.")).toBeTruthy();
  expect(headings()).toEqual(["Actions"]);
});

/** The fan-out rests before it fires, so a term states that it is being read rather than standing
 *  under an empty list that reads as a workspace holding nothing. The line stands until the last
 *  kind lands, because a kind that has not answered yet may still hold rows. */
test("a term states that it is being read until the reads answer", async () => {
  everything();
  await open();
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  expect(found.getByText("Searching…")).toBeTruthy();
  expect(await found.findByRole("option", { name: /Rename the deploy job/ })).toBeTruthy();
  await waitFor(() => expect(found.queryByText("Searching…")).toBeNull());
});

/** A read the suite holds open, so the panel can be read while one kind is still being answered.
 *  Every call waits, and `lands` answers all of them. */
function slow(answer: () => Response): { route: Route; lands: () => void } {
  const waiting: (() => void)[] = [];
  return {
    route: () => new Promise<Response>((resolve) => waiting.push(() => resolve(answer()))),
    lands: () => waiting.splice(0).forEach((go) => go()),
  };
}

/** The reads are one per kind and they answer at their own speeds, so each kind is drawn as it
 *  lands. A slow kind holds back its own rows and nothing else. */
test("a kind stands as soon as it answers, while a slower kind is still being read", async () => {
  const tasks = slow(() => objectIndex(TASK_KIND, [FOUND_TASK]));
  everything({ ["/objects/" + TASK_KIND.kind]: tasks.route });
  await open();
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  expect(await found.findByRole("option", { name: /Rename the deploy job/ })).toBeTruthy();
  expect(found.getByRole("option", { name: /deploy-plan.md/ })).toBeTruthy();
  expect(found.queryByRole("option", { name: /nightly-deploy/ })).toBeNull();
  expect(found.getByText("Searching…")).toBeTruthy();

  tasks.lands();
  expect(await found.findByRole("option", { name: /nightly-deploy/ })).toBeTruthy();
  await waitFor(() => expect(found.queryByText("Searching…")).toBeNull());
});

/** A kind stands where it always stands, whenever it lands: the slowest kind takes its own place in
 *  the list rather than the last one, so rows do not move as the member reads them. */
test("a kind that answers last still stands in its own place", async () => {
  const conversations = slow(() => json({ conversations: [FOUND_CONVERSATION] }));
  everything({ "/conversations$": conversations.route });
  await open();
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  expect(await found.findByRole("option", { name: /nightly-deploy/ })).toBeTruthy();
  expect(headings()).toEqual(["Actions", "Artifacts", "Tasks"]);

  conversations.lands();
  expect(await found.findByRole("option", { name: /Rename the deploy job/ })).toBeTruthy();
  await waitFor(() =>
    expect(headings()).toEqual(["Actions", "Threads", "Artifacts", "Tasks"]),
  );
});

/** A thread is a thread wherever it was found: the rail's own rows and the workspace read's hits
 *  stand in one run under the one word, and a thread the rail already carries is listed once. */
test("the rail's threads and the read's hits stand in one run", async () => {
  wire({
    ...chatsOnWire(RAIL),
    ...QUIET,
    "/conversations$": () => json({ conversations: [FOUND_CONVERSATION, FOUND_THEIR_CONVERSATION] }),
  });
  await open();
  await type("thread");

  const found = within(await screen.findByRole("dialog"));
  expect(await found.findByRole("option", { name: /Rotate the signing key/ })).toBeTruthy();
  expect(headings()).toEqual(["Actions", "Threads"]);
  expect(rowsUnder("Threads")).toEqual([
    "Pick one thread",
    "Rotate the signing keyAssistant",
    "See more history",
  ]);
});

/** Files and sites are two reads standing as one group, so the group is drawn on whichever read
 *  has landed and takes the other's hits when they arrive. */
test("the artifacts group stands on the read that landed and takes the other's hits", async () => {
  const sites = slow(() => objectIndex(SITE_KIND, [owned({ name: "deploy-board" })]));
  everything({ ["/objects/" + SITE_KIND.kind]: sites.route });
  await open();
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  expect(await found.findByRole("option", { name: /deploy-plan.md/ })).toBeTruthy();
  expect(rowsUnder("Artifacts")).toHaveLength(1);

  sites.lands();
  expect(await found.findByRole("option", { name: /deploy-board/ })).toBeTruthy();
  expect(rowsUnder("Artifacts")).toHaveLength(2);
  expect(headings()).toEqual(["Actions", "Threads", "Artifacts", "Tasks"]);
});

test("an empty box reads nothing at all", async () => {
  const { calls } = everything();
  await open();
  await type("  ");

  await waitFor(() => expect(screen.getByRole("combobox", { name: "Search" })).toBeTruthy());
  expect(calls.some((url) => url.includes("q="))).toBe(false);
});

test("the chord opens the palette from anywhere, and Escape shuts it", async () => {
  everything();
  portal();

  await userEvent.keyboard("{Meta>}k{/Meta}");
  expect(await screen.findByRole("combobox", { name: "Search" })).toBeTruthy();

  await userEvent.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

/** The chord that opened the palette walks it: the workspace, the scopes a search can be narrowed
 *  to, and back to the workspace. It never shuts the panel — Escape is what does that — so a member
 *  pressing it twice is where they started rather than where they began. */
test("the chord walks the palette from the workspace to the scopes and back", async () => {
  railed();
  await open();

  await screen.findByRole("dialog");
  expect(foot()).toContain("Launcher");

  await userEvent.keyboard("{Meta>}k{/Meta}");
  expect(await screen.findByRole("option", { name: scopeRow("Assistant") })).toBeTruthy();
  expect(headings()).toEqual(["Threads"]);
  expect(foot()).toContain("Threads");

  await userEvent.keyboard("{Meta>}k{/Meta}");
  expect(await screen.findByRole("option", { name: "Assistant" })).toBeTruthy();
  expect(screen.queryByRole("option", { name: scopeRow("Assistant") })).toBeNull();
  expect(foot()).toContain("Launcher");
});

/** `ctrl+k` is kill-line wherever a field is readline-shaped, so the palette never takes it: the
 *  chord is Meta's alone, and that is what the bar states as the shortcut. */
test("the ctrl chord leaves the palette shut", async () => {
  everything();
  portal();

  await userEvent.keyboard("{Control>}k{/Control}");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByRole("button", { name: "Search" }).getAttribute("aria-keyshortcuts")).toBe(
    "Meta+K",
  );

  await userEvent.keyboard("{Meta>}k{/Meta}");
  expect(await screen.findByRole("combobox", { name: "Search" })).toBeTruthy();
});

/** Opened on nothing, the palette is still worth reading: it states every app the member has and
 *  every place the bar reaches, and it reads nothing until there is a term to read for. The acts
 *  are the term's own — an empty box has nothing to say and nothing to say it about — so the run
 *  stands only once a term does. */
test("an unopened box lists the apps and the places, and reads nothing", async () => {
  const { calls } = everything();
  await open();

  const found = within(await screen.findByRole("dialog"));
  expect(headings()).toEqual(["Applications", "Places"]);
  expect(
    found.getAllByRole("option").map((row) => row.textContent),
  ).toEqual([
    "Artifacts" + ARTIFACTS_PURPOSE,
    "Assistant",
    "Second",
    "Home",
    "Apps",
    "Connectors",
    "Messaging",
    "Workspace",
  ]);
  expect(calls.some((url) => url.includes("q="))).toBe(false);
});

/** An empty box answers with the work itself: every app the member has, and the conversations they
 *  were last in, each under the kind that names it. A conversation is its title and, at the right
 *  of the row, the app holding it — the one fact telling two threads of the same name apart. The
 *  main app names nothing there: it is the workspace's own assistant rather than an app the member
 *  added. The foot names the page and draws the keys that act on it as it stands. */
test("an empty box lists what the member has and what they were saying", async () => {
  railed();
  await open();

  const found = within(await screen.findByRole("dialog"));
  expect(await found.findByRole("option", { name: /Pick one thread/ })).toBeTruthy();
  expect(headings()).toEqual(["Applications", "Threads", "Places"]);
  expect(rowsUnder("Threads")).toEqual([
    "Pick one thread",
    "Rollout notes",
    "Standup in opsSecond",
    "See more history",
  ]);
  expect(foot()).toBe("LauncherOpen↵Actions⌘K");
});

/** Twelve conversations is what the root lists, and the way past them is the same scope the main
 *  app's own row opens: the row closes the run rather than standing among the threads, so the
 *  member reads the conversations first and the way out of them last. */
test("the last thread row opens the main app's scope over all of them", async () => {
  railed();
  await open();
  await screen.findByRole("option", { name: /Pick one thread/ });

  await userEvent.click(screen.getByRole("option", { name: "See more history" }));

  expect(await screen.findByPlaceholderText("Search Assistant threads")).toBeTruthy();
  expect(headings()).toEqual(["Result threads"]);
  expect(foot()).toContain("Assistant threads");
  expect(screen.queryByRole("option", { name: "See more history" })).toBeNull();
});

/** A scope is a search rather than a place: the box goes on reading, narrowed to the app's own
 *  conversations, and the chevron beside it is the way back out. */
test("picking a scope stands the box inside it", async () => {
  railed();
  await open();
  await screen.findByRole("dialog");

  await intoScope("Assistant");

  expect(await screen.findByPlaceholderText("Search Assistant threads")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Back" })).toBeTruthy();
  expect(headings()).toEqual(["Result threads"]);
  expect(foot()).toContain("Assistant threads");
  const rows = screen.getAllByRole("option").map((row) => row.textContent);
  expect(rows.some((row) => row?.startsWith("Pick one thread"))).toBe(true);
  expect(rows.some((row) => row?.startsWith("Rollout notes"))).toBe(true);
  expect(rows.some((row) => row?.startsWith("Standup in ops"))).toBe(false);
});

/** The cursor lands on the first row of the scope the member just entered. cmdk holds the cursor by
 *  value, and the value that reached the scope names no row inside it: left there, the cursor would
 *  stand on nothing, Enter would answer nothing, and an arrow press would be what put it back. */
test("entering a scope leaves the first row under the cursor, and Enter opens it", async () => {
  railed();
  await open();
  await screen.findByRole("dialog");

  await intoScope("Assistant");

  await waitFor(() =>
    expect(document.querySelector("[cmdk-item][aria-selected=true]")?.textContent).toBe(
      "Pick one thread",
    ),
  );
  expect(foot()).toContain("Open");

  await userEvent.keyboard("{Enter}");

  expect(standing()[0]).toBe(homeConversationLane(CONVO_ID));
});

/** A term inside a scope is read against the app's own conversation listing, and what comes back is
 *  a title and nothing else — the same row the rail's own conversations stand as, so a run of them
 *  is read straight down whether the member searched or not. */
test("a term inside a scope lists what the read found, each row its title alone", async () => {
  wire({
    ...chatsOnWire(RAIL),
    ...QUIET,
    "/conversations$": () => json({ conversations: [FOUND_CONVERSATION] }),
  });
  await open();
  await screen.findByRole("dialog");
  await intoScope("Assistant");
  await type("deploy");

  expect(await screen.findByRole("option", { name: "Rename the deploy job" })).toBeTruthy();
  expect(rowsUnder("Result threads")).toEqual(["Rename the deploy job"]);
});

/** A conversation the read found and the rail never carried states whose it is on the wire, so the
 *  filter answers with it too: `Mine` lists the member's own hit alone, and `Shared` the one
 *  another member owns. */
test("the scope's filter narrows a found thread the rail does not carry", async () => {
  wire({
    ...chatsOnWire(RAIL),
    ...QUIET,
    "/conversations$": () =>
      json({ conversations: [FOUND_THEIR_CONVERSATION, FOUND_OWN_CONVERSATION] }),
  });
  await open();
  await screen.findByRole("dialog");
  await intoScope("Assistant");
  await type("deploy");
  await screen.findByRole("option", { name: "Rotate the signing key" });

  await userEvent.click(screen.getByRole("button", { name: "Filter threads" }));
  await userEvent.click(screen.getByRole("menuitemradio", { name: "Mine" }));

  await waitFor(() => expect(rowsUnder("Result threads")).toEqual(["Roll the release back"]));

  await userEvent.click(screen.getByRole("button", { name: "Filter threads" }));
  await userEvent.click(screen.getByRole("menuitemradio", { name: "Shared" }));

  await waitFor(() => expect(rowsUnder("Result threads")).toEqual(["Rotate the signing key"]));
});

/** Whose conversations the scope lists is the member's to choose, and the rail carries the fact
 *  for every thread it holds. */
test("the scope's filter narrows the threads to the member's own and to the shared", async () => {
  railed();
  await open();
  await screen.findByRole("dialog");
  await intoScope("Assistant");
  await screen.findByRole("option", { name: /Pick one thread/ });

  await userEvent.click(screen.getByRole("button", { name: "Filter threads" }));
  expect(
    screen.getAllByRole("menuitemradio").map((entry) => entry.textContent),
  ).toEqual(["All", "Mine", "Shared"]);
  await userEvent.click(screen.getByRole("menuitemradio", { name: "Mine" }));

  await waitFor(() => expect(screen.queryByRole("option", { name: /Rollout notes/ })).toBeNull());
  expect(screen.getByRole("option", { name: /Pick one thread/ })).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Filter threads" }));
  await userEvent.click(screen.getByRole("menuitemradio", { name: "Shared" }));

  await waitFor(() => expect(screen.queryByRole("option", { name: /Pick one thread/ })).toBeNull());
  expect(screen.getByRole("option", { name: /Rollout notes/ })).toBeTruthy();
});

/** The member narrowed the search in one press, so one press widens it again: Escape leaves the
 *  scope before it leaves the palette. */
test("Escape leaves a scope before it shuts the palette", async () => {
  railed();
  await open();
  await screen.findByRole("dialog");
  await intoScope("Assistant");
  await screen.findByPlaceholderText("Search Assistant threads");

  await userEvent.keyboard("{Escape}");
  expect(await screen.findByPlaceholderText("What are you looking for?")).toBeTruthy();

  await userEvent.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

/** Backspace on an empty box is the member rubbing out the last thing they typed, and the scope is
 *  what that was. */
test("Backspace on an empty box leaves the scope", async () => {
  railed();
  await open();
  await screen.findByRole("dialog");
  await intoScope("Assistant");
  await screen.findByPlaceholderText("Search Assistant threads");

  await userEvent.keyboard("{Backspace}");

  expect(await screen.findByPlaceholderText("What are you looking for?")).toBeTruthy();
  expect(screen.getByRole("option", { name: "Assistant" })).toBeTruthy();
});

/** An app row opens a chat with the app, which is a lane of its own at the near end of home rather
 *  than a screen of the app's: the member asked for one more thing to work in, not for the thing
 *  they were reading to be taken away. */
test("the arrow keys move the cursor and Enter takes the row under it", async () => {
  everything();
  await open();

  await screen.findByRole("dialog");
  await userEvent.keyboard("{ArrowDown}");
  await userEvent.keyboard("{ArrowDown}");
  await userEvent.keyboard("{Enter}");

  expect(location.hash).toBe(homeHash({ opens: [SECOND_ID, AGENT_ID] }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

/** A digit takes the row standing at that place, counted from the first group down rather than from
 *  the cursor: the member reads the row and presses its number. */
test("the meta digit takes the row standing at that place", async () => {
  everything();
  await open();
  await screen.findByRole("dialog");

  await userEvent.keyboard("{Meta>}1{/Meta}");

  expect(location.hash).toBe(homeHash({ opens: [ARTIFACTS_APP.id, AGENT_ID] }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

/** The lane enters at the near end, where the member is looking, and nothing they were holding is
 *  shut: an app already standing gets a second instance beside the first rather than the row being
 *  handed back the lane it already had. */
test("an app row stands a new lane at the near end of the row home already holds", async () => {
  everything();
  location.hash = homeHash({ opens: [AGENT_ID] });
  portal();
  await screen.findByRole("region", { name: "Assistant" });
  await userEvent.click(screen.getByRole("button", { name: "Search" }));
  await screen.findByRole("dialog");

  await userEvent.click(screen.getByRole("option", { name: "Assistant" }));

  const second = mintHomeLane(AGENT_ID, [AGENT_ID]);
  expect(location.hash).toBe(homeHash({ opens: [second, AGENT_ID] }));
});

/** A conversation opened beside what home already holds is a lane on home's track, not a screen of
 *  its own — the same act the rail takes when a row is opened with the command held. */
test("the command held over Enter stands the row beside what home holds", async () => {
  railed();
  await open();
  await screen.findByRole("option", { name: /Pick one thread/ });

  await userEvent.keyboard("{ArrowDown}{ArrowDown}{ArrowDown}");
  expect(document.querySelector("[cmdk-item][aria-selected=true]")?.textContent).toContain(
    "Pick one thread",
  );

  await userEvent.keyboard("{Meta>}{Enter}{/Meta}");

  expect(parseHash(location.hash)).toMatchObject({ kind: "home" });
  expect(decodeURIComponent(location.hash).endsWith("c:" + CONVO_ID)).toBe(true);
});

/** The row carries no glyph and states the act in full — the app it speaks to, then the words — and
 *  the foot names the key that runs it while the cursor stands on it, so a member reads the act and
 *  the key that takes it in one line. */
test("the ask row names the app and the term, and the foot names its key", async () => {
  everything();
  await open();
  await type("deploy");

  const row = await screen.findByRole("option", { name: "Ask Assistant: deploy" });
  expect(row.querySelector("svg")).toBeNull();
  expect(rowsUnder("Actions")).toEqual(["Ask Assistant: deploy"]);
  expect(foot()).toBe("LauncherAsk Assistant↵Actions⌘K");

  await screen.findByRole("option", { name: /Rename the deploy job/ });
  await userEvent.keyboard("{ArrowDown}");

  expect(foot()).toBe("LauncherOpen↵Actions⌘K");
});

/** The row says the term to the agent the palette names, which is the main one. The words are said
 *  from a lane of home's own, at the near end where the member is looking: the ask is one more thing
 *  to work in, so nothing they were holding is shut for it. The lane the words are left under is the
 *  key its own composer reads them by — left unread they would be said into a later chat. */
test("the term is said to the agent, by the composer in the lane it lands in", async () => {
  const { calls } = wire({
    ...QUIET,
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: FOUNDED_ID, title: "deploys" }),
  });
  location.hash = "#/";
  portal();
  await userEvent.click(await screen.findByRole("button", { name: "Search" }));
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  await userEvent.click(found.getByRole("option", { name: "Ask Assistant: deploy" }));

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  const said = calls.filter((url) => url.includes("/chat?conversation="));
  expect(said).toHaveLength(1);
  expect(said[0]).toContain("/agents/" + AGENT_ID + "/chat?conversation=new");
  expect(standing()).toEqual([mintHomeLane(AGENT_ID, [AGENT_ID]), AGENT_ID]);
  expect(await screen.findAllByRole("region", { name: "Assistant" })).toHaveLength(2);
});

/** Tab is the key a launcher spends on completing what was typed, and here that is saying it: the
 *  same act the row carries, reached without leaving the box. */
test("Tab says the term the box holds, in a lane of its own", async () => {
  const { calls } = wire({
    ...QUIET,
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: FOUNDED_ID, title: "deploys" }),
  });
  location.hash = "#/";
  portal();
  await userEvent.click(await screen.findByRole("button", { name: "Search" }));
  const box = await type("deploy");

  await userEvent.keyboard("{Tab}");

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  const said = calls.filter((url) => url.includes("/chat?conversation="));
  expect(said).toHaveLength(1);
  expect(said[0]).toContain("/agents/" + AGENT_ID + "/chat?conversation=new");
  expect(standing()).toEqual([mintHomeLane(AGENT_ID, [AGENT_ID]), AGENT_ID]);
  expect(box.isConnected).toBe(false);
});

test("the term is said to the agent the row names, from another agent's start screen", async () => {
  const { calls } = wire({
    ...QUIET,
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: FOUNDED_ID, title: "deploys" }),
  });
  location.hash = "#/new/" + SECOND_ID;
  portal();
  await screen.findByLabelText("Ask UFO");
  await userEvent.click(screen.getByRole("button", { name: "Search" }));
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  await userEvent.click(found.getByRole("option", { name: "Ask Assistant: deploy" }));

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  const said = calls.filter((url) => url.includes("/chat?conversation="));
  expect(said).toHaveLength(1);
  expect(said[0]).toContain("/agents/" + AGENT_ID + "/chat?conversation=new");
  expect(said[0]).not.toContain(SECOND_ID);
  expect(standing()).toEqual([AGENT_ID]);
});

/** The words go to the agent's new chat. A conversation the member is reading belongs to that same
 *  agent and its composer is the one mounted, so an ask taken by the agent alone would be said
 *  there — into the thread they were leaving, over the draft they left in it. */
test("the term founds a new conversation, though the member was reading another", async () => {
  const { calls } = wire({
    ...chatsOnWire([CHAT_ROW]),
    ...QUIET,
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: FOUNDED_ID, title: "deploys" }),
  });
  location.hash = "#/c/" + CONVO_ID;
  portal();
  await screen.findByLabelText("Ask UFO");
  await userEvent.click(screen.getByRole("button", { name: "Search" }));
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  await userEvent.click(found.getByRole("option", { name: "Ask Assistant: deploy" }));

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  const said = calls.filter((url) => url.includes("/chat?conversation="));
  expect(said).toHaveLength(1);
  expect(said[0]).toContain("conversation=new");
  expect(said[0]).not.toContain(CONVO_ID);
  expect(standing()).toEqual([AGENT_ID]);
});

/** The connectors screen takes two reads, and the palette takes the same two: the accounts the
 *  workspace already holds, then the providers it could still connect. Both stand under the one
 *  heading, and each opens the screen at what it names. */
test("a term reaches the accounts the workspace holds and the providers it could connect", async () => {
  wire({
    ...QUIET,
    "/connector-catalog": () =>
      json({
        providers: [
          { name: "github", label: "GitHub" },
          { name: "linear", label: "Linear" },
        ],
        after: null,
      }),
    "/connections": () =>
      json({
        connections: [
          {
            provider: "github",
            account_id: "acme",
            account_label: "acme org",
            owner_email: MEMBER.email,
            shared: true,
            grant: GRANT,
          },
        ],
      }),
  });
  await open();
  await type("acme");

  const found = within(await screen.findByRole("dialog"));
  expect(await found.findByRole("option", { name: /GitHub/ })).toBeTruthy();
  expect(found.getByRole("option", { name: /Linear/ })).toBeTruthy();
  expect(headings()).toContain("Connectors");

  await userEvent.click(found.getByRole("option", { name: /GitHub/ }));
  expect(parseHash(location.hash)).toEqual({
    kind: "section",
    section: "connectors",
    place: { opens: ["connection/" + GRANT] },
  });
});

/** A connector read that refuses is not a thing the member can act on from the box, and a workspace
 *  whose broker is simply unreachable would otherwise put that line under every term they type. The
 *  pool's refusal reads as no accounts, and the catalog answers the group on its own. */
test("a pool that refuses states nothing, and the catalog still answers", async () => {
  wire({
    ...QUIET,
    "/connector-catalog": () =>
      json({ providers: [{ name: "linear", label: "Linear" }], after: null }),
    "/connections": REFUSED,
  });
  await open();
  await type("linear");

  const found = within(await screen.findByRole("dialog"));
  expect(await found.findByRole("option", { name: /Linear/ })).toBeTruthy();
  expect(headings()).toContain("Connectors");
  expect(rowsUnder("Connectors")).toEqual(["LinearNot connected"]);
  expect(found.queryByText(/Error 500/)).toBeNull();
});

/** The rail leads with the launcher: one tile over the tabs, holding every app and thread a tab
 *  could open. There is no second tile searching for the same things beside it. */
test("the rail's leading tile opens the launcher, and no tile searches beside it", async () => {
  atDeskWidth();
  railed();
  portal();

  const rail = within(await screen.findByRole("navigation", { name: "Tabs" }));
  expect(rail.queryByRole("button", { name: "Search" })).toBeNull();
  expect(rail.queryByRole("button", { name: "New tab" })).toBeNull();

  await userEvent.click(rail.getByRole("button", { name: "Launcher" }));

  expect(await screen.findByRole("combobox", { name: "Search" })).toBeTruthy();
  expect(await screen.findByRole("option", { name: /Pick one thread/ })).toBeTruthy();
});
