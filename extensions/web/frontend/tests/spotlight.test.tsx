import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { wakeAppStatus } from "@/lib/appStatusStore";
import { homeConversationLane, homeHash, mintHomeLane, parseHash } from "@/lib/route";
import { REST_MS } from "@/views/Spotlight";

import { AGENT, AGENT_ID, atPhoneWidth, CHAT_ROW, chatsOnWire, CONVO_ID, json, MEMBER, objectIndex, owned, type Route, SECOND, SECOND_ID, SITE_KIND, StreamFake, TASK_KIND, TRIGGER_KIND, TURN_ID, useStreamFake, wire } from "./harness";

beforeEach(() => {
  location.hash = "";
  atPhoneWidth();
  useStreamFake();
});

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

/** Wired ahead of the index it was found in: the stub matches on the path it is given, and the index's
 *  own path is a prefix of the record's. */
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

const QUIET = {
  "/slots": () => json({ slots: [] }),
  "/objects/artifact": () => json({ objects: [] }),
  "/workspace/memory": () => json({ available: true, kinds: [], matches: [] }),
  ["/objects/" + TASK_KIND.kind]: () => objectIndex(TASK_KIND, []),
  ["/objects/" + TRIGGER_KIND.kind]: () => objectIndex(TRIGGER_KIND, []),
  ["/objects/" + SITE_KIND.kind]: () => objectIndex(SITE_KIND, []),
  "/transcript": () => json({ messages: [] }),
};

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

const RAIL = [MINE_CHAT, SHARED_CHAT, SLACK_CHAT];

function railed() {
  return wire({ ...chatsOnWire(RAIL), ...QUIET });
}

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

/** cmdk hides the heading element itself and points the group's label at it, so the order the runs
 *  stand in is read off the elements rather than off a role. */
function headings() {
  return [...document.querySelectorAll("[cmdk-group-heading]")].map((head) => head.textContent);
}

function rowsUnder(heading: string): string[] {
  const run = document.querySelector('[cmdk-group][data-value="' + heading + '"]');
  if (!run) throw new Error("the palette draws no " + heading + " run");
  return [...run.querySelectorAll("[cmdk-item]")].map((row) => String(row.textContent));
}

function standing(): string[] {
  const route = parseHash(location.hash);
  if (route?.kind !== "home") throw new Error("the palette did not land home: " + location.hash);
  return route.place.opens ?? [];
}

function foot(): string {
  const bar = document.querySelector("[data-slot=command-foot]");
  if (!bar) throw new Error("the palette draws no foot");
  return String(bar.textContent);
}

async function intoScope(label: string) {
  await userEvent.keyboard("{Meta>}k{/Meta}");
  await userEvent.click(await screen.findByRole("option", { name: scopeRow(label) }));
}

function scopeRow(label: string): RegExp {
  return new RegExp("^Search " + label + " threads");
}

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

test("a status read that moves a working app leaves the standing term alone", async () => {
  let activity = "reading the repo";
  const { calls } = everything({
    "/api/agents/status": () =>
      json({
        statuses: [
          {
            agent_id: AGENT_ID,
            turn: "running",
            activity,
            last_active_at: null,
            last_failed: false,
          },
        ],
      }),
  });
  await open();
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  expect(await found.findByRole("option", { name: /Rename the deploy job/ })).toBeTruthy();
  const searched = () => calls.filter((url) => url.includes("q=deploy")).length;
  const before = searched();
  expect(before).toBeGreaterThan(0);

  vi.useFakeTimers();
  try {
    activity = "writing the plan";
    await act(async () => {
      wakeAppStatus();
      await vi.advanceTimersByTimeAsync(REST_MS * 5);
    });

    expect(searched()).toBe(before);
    expect(found.getByRole("option", { name: /Rename the deploy job/ })).toBeTruthy();
    expect(found.queryByText("Searching…")).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});

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


/** An object's name is unique under its own agent, not across the workspace, so two agents may each
 *  hold a `nightly-deploy`. */
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

test("a term states that it is being read until the reads answer", async () => {
  everything();
  await open();
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  expect(found.getByText("Searching…")).toBeTruthy();
  expect(await found.findByRole("option", { name: /Rename the deploy job/ })).toBeTruthy();
  await waitFor(() => expect(found.queryByText("Searching…")).toBeNull());
});

function slow(answer: () => Response): { route: Route; lands: () => void } {
  const waiting: (() => void)[] = [];
  return {
    route: () => new Promise<Response>((resolve) => waiting.push(() => resolve(answer()))),
    lands: () => waiting.splice(0).forEach((go) => go()),
  };
}

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

test("the meta digit takes the row standing at that place", async () => {
  everything();
  await open();
  await screen.findByRole("dialog");

  await userEvent.keyboard("{Meta>}1{/Meta}");

  expect(location.hash).toBe(homeHash({ opens: [ARTIFACTS_APP.id, AGENT_ID] }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

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

const UNTITLED_CONVERSATION = { ...FOUND_CONVERSATION, description: "" };

const TRIGGER_NAME = "github-30847ee49f0a4c7f8d2e1a3b4c5d6e7f-1a2b3c4d";

const FOUND_TRIGGER = owned({
  name: TRIGGER_NAME,
  summary: "github-30847ee4 — issues",
  conversation: CONVO_ID,
  delivery: "current",
  mine: true,
});

test("a thread the wire titles with nothing reads as who it is with, never as its id", async () => {
  everything({ "/conversations$": () => json({ conversations: [UNTITLED_CONVERSATION] }) });
  await open();
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  expect(await found.findByRole("option", { name: /Workspace/ })).toBeTruthy();
  expect(found.queryByText(CONVO_ID)).toBeNull();
});

test("a source trigger reads as the feed it watches, not as the name its conversation's hex is in", async () => {
  everything({
    ["/objects/" + TRIGGER_KIND.kind]: () => objectIndex(TRIGGER_KIND, [FOUND_TRIGGER]),
  });
  await open();
  await type("github");

  const found = within(await screen.findByRole("dialog"));
  expect(await found.findByRole("option", { name: /github-30847ee4 — issues/ })).toBeTruthy();
  expect(found.queryByText(TRIGGER_NAME)).toBeNull();
});
