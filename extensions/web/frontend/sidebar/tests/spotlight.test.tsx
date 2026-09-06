import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import { AGENT, AGENT_ID, CONVO_ID, json, MEMBER, objectIndex, owned, type Route, SECOND, SECOND_ID, SITE_KIND, TASK_KIND, TRIGGER_KIND, useStreamFake, wire } from "./harness";

beforeEach(() => {
  location.hash = "";
  useStreamFake();
});

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

/** Every read the spotlight fans out to, each answering the one term. */
function everything(extra: Record<string, Route> = {}) {
  return wire({
    ["/objects/" + TASK_KIND.kind + "/nightly-deploy"]: () => json(FOUND_TASK_DETAIL),
    "/slots": () => json({ slots: [] }),
    "/conversations$": () => json({ conversations: [FOUND_CONVERSATION] }),
    "/objects/artifact": () => json({ objects: [FOUND_FILE] }),
    ["/objects/" + TASK_KIND.kind]: () => objectIndex(TASK_KIND, [FOUND_TASK]),
    ["/objects/" + TRIGGER_KIND.kind]: () => objectIndex(TRIGGER_KIND, []),
    ["/objects/" + SITE_KIND.kind]: () => objectIndex(SITE_KIND, []),
    "/transcript": () => json({ messages: [] }),
    ...extra,
  });
}

function nothing() {
  return wire({
    "/slots": () => json({ slots: [] }),
    "/objects/artifact": () => json({ objects: [] }),
    "/workspace/memory": () => json({ available: true, kinds: [], matches: [] }),
    ["/objects/" + TASK_KIND.kind]: () => objectIndex(TASK_KIND, []),
    ["/objects/" + TRIGGER_KIND.kind]: () => objectIndex(TRIGGER_KIND, []),
    ["/objects/" + SITE_KIND.kind]: () => objectIndex(SITE_KIND, []),
    "/transcript": () => json({ messages: [] }),
  });
}

/** The shipped app whose pane holds the workspace's files: a file hit lands on the app that reads
 *  its kind. A task hit lands on the workspace's own tasks tab, which needs no app. */
const ARTIFACTS_APP = {
  id: "7f1b9f6e-9f30-4f8f-9a6e-1d9d1c2b3a41",
  name: "artifacts",
  model: "auto",
  main: false,
  icon: "stele",
  app: "artifacts",
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

test("one term reaches every kind the workspace holds, each hit under its own heading", async () => {
  const { calls } = everything();
  await open();
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  expect(await found.findByRole("option", { name: /Rename the deploy job/ })).toBeTruthy();
  expect(found.getByRole("option", { name: /deploy-plan.md/ })).toBeTruthy();
  expect(found.getByRole("option", { name: /nightly-deploy/ })).toBeTruthy();
  expect(headings()).toEqual(["Threads", "Artifacts", "Tasks"]);

  const asked = calls.filter((url) => url.includes("q=deploy"));
  expect(asked.some((url) => url.includes("/objects/artifact"))).toBe(true);
  expect(asked.some((url) => url.includes("/workspace/memory"))).toBe(false);
  expect(asked.some((url) => url.includes("/agents/" + AGENT_ID + "/conversations"))).toBe(true);
  expect(asked.some((url) => url.includes("/agents/" + SECOND_ID + "/conversations"))).toBe(true);
  expect(asked.some((url) => url.includes("/objects/" + TASK_KIND.kind))).toBe(true);
});

/** An agent is named by the boot payload the shell already holds, so it needs no read of its own. */
test("an agent matches from the payload the shell holds, under its own heading", async () => {
  const { calls } = everything();
  await open();
  await type("second");

  const found = within(await screen.findByRole("dialog"));
  expect(await found.findByRole("option", { name: "Second opus" })).toBeTruthy();
  expect(headings()[0]).toBe("Apps");
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

  expect(location.hash).toBe("#/c/" + CONVO_ID);
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
    "/slots": () => json({ slots: [] }),
    "/objects/artifact": () => json({ objects: [] }),
    "/workspace/memory": () => json({ available: true, kinds: [], matches: [] }),
    ["/objects/" + TASK_KIND.kind]: () => objectIndex(TASK_KIND, [FOUND_TASK, second]),
    ["/objects/" + TRIGGER_KIND.kind]: () => objectIndex(TRIGGER_KIND, []),
    ["/objects/" + SITE_KIND.kind]: () => objectIndex(SITE_KIND, []),
    "/transcript": () => json({ messages: [] }),
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
    "/slots": () => json({ slots: [] }),
    "/conversations$": () => json({ conversations: [FOUND_CONVERSATION] }),
    "/objects/artifact": () => new Response("nope", { status: 503 }),
    "/workspace/memory": () => json({ available: true, kinds: [], matches: [] }),
    ["/objects/" + TASK_KIND.kind]: () => objectIndex(TASK_KIND, []),
    ["/objects/" + TRIGGER_KIND.kind]: () => objectIndex(TRIGGER_KIND, []),
    ["/objects/" + SITE_KIND.kind]: () => objectIndex(SITE_KIND, []),
    "/transcript": () => json({ messages: [] }),
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
  expect(headings()).toEqual([]);
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
 *  lands, and the slow one takes its own place in the list when it arrives. */
test("a kind stands as soon as it answers, while a slower kind is still being read", async () => {
  const conversations = slow(() => json({ conversations: [FOUND_CONVERSATION] }));
  everything({ "/conversations$": conversations.route });
  await open();
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  expect(await found.findByRole("option", { name: /nightly-deploy/ })).toBeTruthy();
  expect(found.queryByRole("option", { name: /Rename the deploy job/ })).toBeNull();
  expect(found.getByText("Searching…")).toBeTruthy();

  conversations.lands();
  expect(await found.findByRole("option", { name: /Rename the deploy job/ })).toBeTruthy();
  await waitFor(() =>
    expect(headings()).toEqual(["Threads", "Artifacts", "Tasks"]),
  );
});

test("an empty box reads nothing at all", async () => {
  const { calls } = everything();
  await open();
  await type("  ");

  await waitFor(() => expect(screen.getByRole("combobox", { name: "Search" })).toBeTruthy());
  expect(calls.some((url) => url.includes("q="))).toBe(false);
});

test("the chord opens the palette from anywhere, and closes it again", async () => {
  everything();
  portal();

  await userEvent.keyboard("{Meta>}k{/Meta}");
  expect(await screen.findByRole("combobox", { name: "Search" })).toBeTruthy();

  await userEvent.keyboard("{Meta>}k{/Meta}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
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

/** Opened on nothing, the palette is still worth reading: it states what the member can do and
 *  every place the bar reaches, and it reads nothing until there is a term to read for. */
test("an unopened term lists what to do and where to go, and reads nothing", async () => {
  const { calls } = everything();
  await open();

  const found = within(await screen.findByRole("dialog"));
  expect(found.getByRole("option", { name: "New chat" })).toBeTruthy();
  expect(headings()).toEqual(["Actions", "Places"]);
  expect(
    found.getAllByRole("option").map((row) => row.textContent),
  ).toEqual(["New chat", "Chat", "Apps", "Connectors", "Workspace"]);
  expect(calls.some((url) => url.includes("q="))).toBe(false);
});

test("the arrow keys move the cursor and Enter takes the row under it", async () => {
  everything();
  await open();

  await screen.findByRole("dialog");
  await userEvent.keyboard("{ArrowDown}");
  await userEvent.keyboard("{Enter}");

  expect(location.hash).toBe("#/");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});
