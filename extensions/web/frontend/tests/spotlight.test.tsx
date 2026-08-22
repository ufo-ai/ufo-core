import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { parseHash } from "@/lib/route";

import {
  AGENT,
  AGENT_ID,
  CHAT_ROW,
  CONVO_ID,
  MEMBER,
  SECOND,
  SECOND_ID,
  SITE_KIND,
  StreamFake,
  TASK_KIND,
  TRIGGER_KIND,
  TURN_ID,
  json,
  objectIndex,
  owned,
  useStreamFake,
  wire,
} from "./harness";

beforeEach(() => {
  location.hash = "";
  useStreamFake();
});

const FOUNDED_ID = "77777777-7777-4777-8777-777777777777";

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
};

const FOUND_FILE = {
  id: "f1",
  filename: "deploy-plan.md",
  subject: null,
  media_type: "text/markdown",
  size_bytes: 64,
  created_at: "2026-08-14T09:00:00",
  url: "/dl/deploy-plan.md",
  preview_url: null,
  owner_email: MEMBER.email,
};

const FOUND_MEMORY = {
  text: "the deploy runbook lives in ops/",
  kind: "fact",
  ref: "memory/1",
  created_at: "2026-08-01T09:00:00",
  subject: "shared",
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
function everything() {
  return wire({
    ["/objects/" + TASK_KIND.kind + "/nightly-deploy"]: () => json(FOUND_TASK_DETAIL),
    "/slots": () => json({ slots: [] }),
    "/conversations$": () => json({ conversations: [FOUND_CONVERSATION] }),
    "/workspace/artifacts": () => json({ artifacts: [FOUND_FILE] }),
    "/workspace/memory": () => json({ available: true, kinds: [], matches: [FOUND_MEMORY] }),
    ["/objects/" + TASK_KIND.kind]: () => objectIndex(TASK_KIND, [FOUND_TASK]),
    ["/objects/" + TRIGGER_KIND.kind]: () => objectIndex(TRIGGER_KIND, []),
    ["/objects/" + SITE_KIND.kind]: () => objectIndex(SITE_KIND, []),
    "/transcript": () => json({ messages: [] }),
  });
}

function nothing() {
  return wire({
    "/slots": () => json({ slots: [] }),
    "/workspace/artifacts": () => json({ artifacts: [] }),
    "/workspace/memory": () => json({ available: true, kinds: [], matches: [] }),
    ["/objects/" + TASK_KIND.kind]: () => objectIndex(TASK_KIND, []),
    ["/objects/" + TRIGGER_KIND.kind]: () => objectIndex(TRIGGER_KIND, []),
    ["/objects/" + SITE_KIND.kind]: () => objectIndex(SITE_KIND, []),
    "/transcript": () => json({ messages: [] }),
  });
}

function portal() {
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);
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
  expect(found.getByRole("option", { name: /the deploy runbook lives in ops\// })).toBeTruthy();
  expect(found.getByRole("option", { name: /nightly-deploy/ })).toBeTruthy();
  expect(headings()).toEqual(["Actions", "Conversations", "Artifacts", "Memory", "Tasks"]);

  const asked = calls.filter((url) => url.includes("q=deploy"));
  expect(asked.some((url) => url.includes("/workspace/artifacts"))).toBe(true);
  expect(asked.some((url) => url.includes("/workspace/memory"))).toBe(true);
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
  expect(headings().slice(0, 2)).toEqual(["Actions", "Apps"]);
  expect(calls.some((url) => url.includes("/api/agents?q="))).toBe(false);
});

/** Every memory match opens the same screen, so a group deduped by where it lands would keep one
 *  match and drop the rest. What the member searched for is the matches, not the screen. */
test("every memory match stands, though they all open the one memory screen", async () => {
  const second = { ...FOUND_MEMORY, text: "deploys are announced in #ops", ref: "memory/2" };
  wire({
    "/slots": () => json({ slots: [] }),
    "/workspace/artifacts": () => json({ artifacts: [] }),
    "/workspace/memory": () =>
      json({ available: true, kinds: [], matches: [FOUND_MEMORY, second] }),
    ["/objects/" + TASK_KIND.kind]: () => objectIndex(TASK_KIND, []),
    ["/objects/" + TRIGGER_KIND.kind]: () => objectIndex(TRIGGER_KIND, []),
    ["/objects/" + SITE_KIND.kind]: () => objectIndex(SITE_KIND, []),
    "/transcript": () => json({ messages: [] }),
  });
  await open();
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  expect(await found.findByRole("option", { name: /the deploy runbook lives in ops\// })).toBeTruthy();
  expect(found.getByRole("option", { name: /deploys are announced in #ops/ })).toBeTruthy();
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

test("an artifact hit opens the artifacts screen on that file", async () => {
  everything();
  await open();
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  await userEvent.click(await found.findByRole("option", { name: /deploy-plan.md/ }));

  expect(location.hash).toBe("#/artifacts?q=deploy&open=" + FOUND_FILE.id);
});

/** A filename is whatever the agent sharing the file called it — the character a track is written
 *  with included, and at whatever length. One such file among the answers is one hit to draw, never
 *  a search the member reads as broken: every other kind still stands, and the file opens where
 *  files open, because a lane is named by the file's own id. */
const ODD_FOUND_NAME = "deploy~v2-" + "and-the-whole-quarter-".repeat(20) + "final.md";

test("a found file named oddly and past what a lane id holds leaves every hit standing", async () => {
  wire({
    "/slots": () => json({ slots: [] }),
    "/conversations$": () => json({ conversations: [FOUND_CONVERSATION] }),
    "/workspace/artifacts": () =>
      json({ artifacts: [{ ...FOUND_FILE, id: "f2", filename: ODD_FOUND_NAME }] }),
    "/workspace/memory": () => json({ available: true, kinds: [], matches: [FOUND_MEMORY] }),
    ["/objects/" + TASK_KIND.kind]: () => objectIndex(TASK_KIND, []),
    ["/objects/" + TRIGGER_KIND.kind]: () => objectIndex(TRIGGER_KIND, []),
    ["/objects/" + SITE_KIND.kind]: () => objectIndex(SITE_KIND, []),
    "/transcript": () => json({ messages: [] }),
  });
  await open();
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  expect(await found.findByRole("option", { name: new RegExp(ODD_FOUND_NAME) })).toBeTruthy();
  expect(found.getByRole("option", { name: /Rename the deploy job/ })).toBeTruthy();
  expect(found.getByRole("option", { name: /the deploy runbook lives in ops\// })).toBeTruthy();
  expect(headings()).toEqual(["Actions", "Conversations", "Artifacts", "Memory"]);

  await userEvent.click(found.getByRole("option", { name: new RegExp(ODD_FOUND_NAME) }));

  expect(location.hash).toBe("#/artifacts?q=deploy&open=f2");
  expect(parseHash(location.hash)).toMatchObject({ kind: "section", section: "artifacts" });
});

/** A site belongs to the workspace, not to an agent, so its read names one agent — as the
 *  artifacts screen's does. Fanning it out would list the one site once per agent. It is listed
 *  and opened where sites live, which is the artifacts screen, never the radar feed. */
test("a hosted site stands once, under artifacts, and opens there", async () => {
  const site = owned({ name: "deploy-notes", summary: "a page about deploys", site_url: "s" });
  const record = {
    ...SITE_KIND,
    name: "deploy-notes",
    summary: "a page about deploys",
    spec: { visibility: "workspace" },
    status: { conversation: CONVO_ID, visibility: "workspace", site_url: "s", owner_email: null },
    links: [],
    created_at: "2026-08-14T09:00:00Z",
    updated_at: null,
  };
  const { calls } = wire({
    ["/objects/" + SITE_KIND.kind + "/deploy-notes"]: () => json(record),
    "/slots": () => json({ slots: [] }),
    "/workspace/artifacts": () => json({ artifacts: [] }),
    "/workspace/memory": () => json({ available: true, kinds: [], matches: [] }),
    ["/objects/" + TASK_KIND.kind]: () => objectIndex(TASK_KIND, []),
    ["/objects/" + TRIGGER_KIND.kind]: () => objectIndex(TRIGGER_KIND, []),
    ["/objects/" + SITE_KIND.kind]: () => objectIndex(SITE_KIND, [site]),
    "/transcript": () => json({ messages: [] }),
  });
  await open();
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  const rows = await found.findAllByRole("option", { name: /deploy-notes/ });
  expect(rows).toHaveLength(1);
  const siteReads = calls.filter((url) => url.includes("/objects/" + SITE_KIND.kind));
  expect(siteReads).toHaveLength(1);
  expect(siteReads[0]).toContain("agent=" + AGENT_ID);
  // Sites are listed where sites live: the artifacts screen, whose filter holds a Sites family.
  expect(headings()).toEqual(["Actions", "Artifacts"]);

  await userEvent.click(rows[0]);

  const opened = decodeURIComponent(location.hash);
  expect(opened).toContain("#/artifacts?");
  expect(opened).toContain("chip=Sites");
  expect(opened).toContain("object/" + AGENT_ID + "/site/deploy-notes");
  expect(opened).not.toContain("tasks");
});

/** An object's name is unique under its own agent, not across the workspace, so two agents may
 *  each hold a `nightly-deploy`. Both stand, and each hit opens the lane naming its own agent: the
 *  search cannot contradict the index it reads. */
test("two agents' same-named records both stand, each opening its own", async () => {
  const second = owned({ ...FOUND_TASK, mine: false }, SECOND);
  wire({
    ["/objects/" + TASK_KIND.kind + "/nightly-deploy"]: () => json(FOUND_TASK_DETAIL),
    "/slots": () => json({ slots: [] }),
    "/workspace/artifacts": () => json({ artifacts: [] }),
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
  expect(decodeURIComponent(location.hash)).toContain(
    "object/" + SECOND_ID + "/" + TASK_KIND.kind + "/nightly-deploy",
  );
});

test("a task hit opens that record on the tasks screen", async () => {
  everything();
  await open();
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  await userEvent.click(await found.findByRole("option", { name: /nightly-deploy/ }));

  const opened = decodeURIComponent(location.hash);
  expect(opened).toContain("#/tasks?");
  expect(opened).toContain("chip=" + TASK_KIND.kind);
  expect(opened).toContain("object/" + AGENT_ID + "/" + TASK_KIND.kind + "/nightly-deploy");
});

/** A read that refused is not a kind with no hits: the group states the refusal, so the member
 *  never reads a searched workspace as an empty one. */
test("a read that fails states so under its own heading, and the others still answer", async () => {
  wire({
    "/slots": () => json({ slots: [] }),
    "/conversations$": () => json({ conversations: [FOUND_CONVERSATION] }),
    "/workspace/artifacts": () => new Response("nope", { status: 503 }),
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
  expect(headings()).toEqual(["Actions"]);
});

/** The fan-out rests before it fires, so a term states that it is being read rather than standing
 *  under an empty list that reads as a workspace holding nothing. */
test("a term states that it is being read until the reads answer", async () => {
  everything();
  await open();
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  expect(found.getByText("Searching…")).toBeTruthy();
  expect(await found.findByRole("option", { name: /Rename the deploy job/ })).toBeTruthy();
  expect(found.queryByText("Searching…")).toBeNull();
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
  ).toEqual([
    "New chat",
    "Chat",
    "Apps",
    "Wiki",
    "Artifacts",
    "Radar",
    "Tasks",
    "Connectors",
    "Workspace",
  ]);
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

test("a place opens the screen it names", async () => {
  everything();
  await open();
  await type("radar");

  const found = within(await screen.findByRole("dialog"));
  await userEvent.click(found.getByRole("option", { name: "Radar" }));

  expect(location.hash).toBe("#/radar");
});

/** The words are the member's own and they pressed Enter on them, so the row says them: it lands on
 *  a new chat with the term already sent, not with the box filled and waiting for a second press.
 *  The conversation is founded by the composer on the screen, so it is theirs like any other. */
test("the term is said to the agent, by the composer on the screen it lands on", async () => {
  wire({
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "deploy" }),
    "/slots": () => json({ slots: [] }),
    "/conversations$": () => json({ conversations: [FOUND_CONVERSATION] }),
    "/workspace/artifacts": () => json({ artifacts: [] }),
    "/workspace/memory": () => json({ available: true, kinds: [], matches: [] }),
    ["/objects/" + TASK_KIND.kind]: () => objectIndex(TASK_KIND, []),
    ["/objects/" + TRIGGER_KIND.kind]: () => objectIndex(TRIGGER_KIND, []),
    ["/objects/" + SITE_KIND.kind]: () => objectIndex(SITE_KIND, []),
    "/transcript": () => json({ messages: [] }),
  });
  await open();
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  await userEvent.click(found.getByRole("option", { name: "assistant: deploy" }));

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  expect(StreamFake.last().url).toBe("/surface/web/turns/" + TURN_ID + "/stream");
  const composer = (await screen.findByLabelText("Message the app")) as HTMLTextAreaElement;
  expect(composer.value).toBe("");
});

/** The row says the term to the agent the palette names, which is the main one, from any screen —
 *  including the start screen of another agent. One pane stands for every start screen, so the route
 *  renames the agent of the composer already on the screen rather than mounting a second one, and
 *  the words must be read under that new name. Left unread they would be said into a later chat. */
test("the term is said to the agent the row names, from another agent's start screen", async () => {
  const { calls } = wire({
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: FOUNDED_ID, title: "deploys" }),
    "/slots": () => json({ slots: [] }),
    "/workspace/artifacts": () => json({ artifacts: [] }),
    "/workspace/memory": () => json({ available: true, kinds: [], matches: [] }),
    ["/objects/" + TASK_KIND.kind]: () => objectIndex(TASK_KIND, []),
    ["/objects/" + TRIGGER_KIND.kind]: () => objectIndex(TRIGGER_KIND, []),
    ["/objects/" + SITE_KIND.kind]: () => objectIndex(SITE_KIND, []),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/new/" + SECOND_ID;
  portal();
  await screen.findByLabelText("Message the app");
  await userEvent.click(screen.getByRole("button", { name: "Search" }));
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  await userEvent.click(found.getByRole("option", { name: "assistant: deploy" }));

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  const said = calls.filter((url) => url.includes("/chat?conversation="));
  expect(said).toHaveLength(1);
  expect(said[0]).toContain("/agents/" + AGENT_ID + "/chat?conversation=new");
  expect(said[0]).not.toContain(SECOND_ID);
  expect(location.hash).toBe("#/c/" + FOUNDED_ID);
  const composer = (await screen.findByLabelText("Message the app")) as HTMLTextAreaElement;
  expect(composer.value).toBe("");
});

/** The words go to the agent's new chat. A conversation the member is reading belongs to that same
 *  agent and its composer is the one mounted, so an ask taken by the agent alone would be said
 *  there — into the thread they were leaving, over the draft they left in it. */
test("the term founds a new conversation, though the member was reading another", async () => {
  const { calls } = wire({
    "/api/chats": () => json({ chats: [CHAT_ROW] }),
    "/slots": () => json({ slots: [] }),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: FOUNDED_ID, title: "deploys" }),
    "/workspace/artifacts": () => json({ artifacts: [] }),
    "/workspace/memory": () => json({ available: true, kinds: [], matches: [] }),
    ["/objects/" + TASK_KIND.kind]: () => objectIndex(TASK_KIND, []),
    ["/objects/" + TRIGGER_KIND.kind]: () => objectIndex(TRIGGER_KIND, []),
    ["/objects/" + SITE_KIND.kind]: () => objectIndex(SITE_KIND, []),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/c/" + CONVO_ID;
  portal();
  await screen.findByLabelText("Message the app");
  await userEvent.click(screen.getByRole("button", { name: "Search" }));
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  await userEvent.click(found.getByRole("option", { name: "assistant: deploy" }));

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  const said = calls.filter((url) => url.includes("/chat?conversation="));
  expect(said).toHaveLength(1);
  expect(said[0]).toContain("conversation=new");
  expect(said[0]).not.toContain(CONVO_ID);
  expect(location.hash).toBe("#/c/" + FOUNDED_ID);
});
