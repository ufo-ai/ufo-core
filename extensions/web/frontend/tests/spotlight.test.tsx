import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import {
  AGENT,
  AGENT_ID,
  CONVO_ID,
  MEMBER,
  SECOND,
  SECOND_ID,
  SITE_KIND,
  TASK_KIND,
  TRIGGER_KIND,
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
    "/conversations": () => json({ conversations: [FOUND_CONVERSATION] }),
    "/workspace/artifacts": () => json({ artifacts: [FOUND_FILE] }),
    "/workspace/memory": () => json({ available: true, kinds: [], matches: [FOUND_MEMORY] }),
    ["/objects/" + TASK_KIND.kind]: () => objectIndex(TASK_KIND, [FOUND_TASK]),
    ["/objects/" + TRIGGER_KIND.kind]: () => objectIndex(TRIGGER_KIND, []),
    ["/objects/" + SITE_KIND.kind]: () => objectIndex(SITE_KIND, []),
    "/transcript": () => json({ messages: [] }),
  });
}

function open() {
  render(<App agents={[AGENT, SECOND]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
  return userEvent.click(screen.getByRole("button", { name: "Search" }));
}

async function type(term: string) {
  const box = await screen.findByRole("searchbox", { name: "Search" });
  await userEvent.type(box, term);
  return box;
}

test("one term reaches every kind the workspace holds, each hit under its own heading", async () => {
  const { calls } = everything();
  await open();
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  expect(await found.findByRole("button", { name: /Rename the deploy job/ })).toBeTruthy();
  expect(found.getByRole("button", { name: /deploy-plan.md/ })).toBeTruthy();
  expect(found.getByRole("button", { name: /the deploy runbook lives in ops\// })).toBeTruthy();
  expect(found.getByRole("button", { name: /nightly-deploy/ })).toBeTruthy();
  expect(found.getAllByRole("heading", { level: 3 }).map((head) => head.textContent)).toEqual([
    "Conversations",
    "Artifacts",
    "Memory",
    "Radar",
  ]);

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
  expect(await found.findByRole("button", { name: /second/ })).toBeTruthy();
  expect(found.getAllByRole("heading", { level: 3 })[0].textContent).toBe("Apps");
  expect(calls.some((url) => url.includes("/api/agents?q="))).toBe(false);
});

/** Every memory match opens the same screen, so a group deduped by where it lands would keep one
 *  match and drop the rest. What the member searched for is the matches, not the screen. */
test("every memory match stands, though they all open the one memory screen", async () => {
  const second = { ...FOUND_MEMORY, text: "deploys are announced in #ops", ref: "memory/2" };
  wire({
    "/conversations": () => json({ conversations: [] }),
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
  expect(await found.findByRole("button", { name: /the deploy runbook lives in ops\// })).toBeTruthy();
  expect(found.getByRole("button", { name: /deploys are announced in #ops/ })).toBeTruthy();
});

test("a hit opens the place that holds it", async () => {
  everything();
  await open();
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  await userEvent.click(await found.findByRole("button", { name: /Rename the deploy job/ }));

  expect(location.hash).toBe("#/c/" + CONVO_ID);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

test("an artifact hit opens the artifacts screen on that file", async () => {
  everything();
  await open();
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  await userEvent.click(await found.findByRole("button", { name: /deploy-plan.md/ }));

  expect(location.hash).toContain("#/artifacts?");
  expect(decodeURIComponent(location.hash)).toContain("q=deploy");
  expect(decodeURIComponent(location.hash)).toContain("deploy-plan.md");
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
    "/conversations": () => json({ conversations: [] }),
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
  const rows = await found.findAllByRole("button", { name: /deploy-notes/ });
  expect(rows).toHaveLength(1);
  const siteReads = calls.filter((url) => url.includes("/objects/" + SITE_KIND.kind));
  expect(siteReads).toHaveLength(1);
  expect(siteReads[0]).toContain("agent=" + AGENT_ID);
  // Sites are listed where sites live: the artifacts screen, whose filter holds a Sites family.
  expect(found.getAllByRole("heading", { level: 3 }).map((head) => head.textContent)).toEqual([
    "Artifacts",
  ]);

  await userEvent.click(rows[0]);

  const opened = decodeURIComponent(location.hash);
  expect(opened).toContain("#/artifacts?");
  expect(opened).toContain("chip=Sites");
  expect(opened).toContain("object/site/deploy-notes");
  expect(opened).not.toContain("radar");
});

/** An object's name is unique under its own agent, not across the workspace, so two agents may
 *  each hold a `nightly-deploy`. Both stand: the search cannot contradict the index it reads. */
test("two agents' same-named records both stand, each opening its own", async () => {
  const second = owned({ ...FOUND_TASK, mine: false }, SECOND);
  wire({
    ["/objects/" + TASK_KIND.kind + "/nightly-deploy"]: () => json(FOUND_TASK_DETAIL),
    "/conversations": () => json({ conversations: [] }),
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
  const rows = await found.findAllByRole("button", { name: /nightly-deploy/ });
  expect(rows).toHaveLength(2);

  await userEvent.click(rows[1]);
  expect(decodeURIComponent(location.hash)).toContain("agent=" + SECOND_ID);
});

test("a radar hit opens that record in the radar feed", async () => {
  everything();
  await open();
  await type("deploy");

  const found = within(await screen.findByRole("dialog"));
  await userEvent.click(await found.findByRole("button", { name: /nightly-deploy/ }));

  const opened = decodeURIComponent(location.hash);
  expect(opened).toContain("#/radar?");
  expect(opened).toContain("chip=" + TASK_KIND.kind);
  expect(opened).toContain("object/" + TASK_KIND.kind + "/nightly-deploy");
  expect(opened).toContain("agent=" + AGENT_ID);
});

/** A read that refused is not a kind with no hits: the group states the refusal, so the member
 *  never reads a searched workspace as an empty one. */
test("a read that fails states so under its own heading, and the others still answer", async () => {
  wire({
    "/conversations": () => json({ conversations: [FOUND_CONVERSATION] }),
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
  expect(found.getByRole("button", { name: /Rename the deploy job/ })).toBeTruthy();
});

test("a term nothing answers says so once, not once per kind", async () => {
  wire({
    "/conversations": () => json({ conversations: [] }),
    "/workspace/artifacts": () => json({ artifacts: [] }),
    "/workspace/memory": () => json({ available: true, kinds: [], matches: [] }),
    ["/objects/" + TASK_KIND.kind]: () => objectIndex(TASK_KIND, []),
    ["/objects/" + TRIGGER_KIND.kind]: () => objectIndex(TRIGGER_KIND, []),
    ["/objects/" + SITE_KIND.kind]: () => objectIndex(SITE_KIND, []),
    "/transcript": () => json({ messages: [] }),
  });
  await open();
  await type("nothing matches this");

  const found = within(await screen.findByRole("dialog"));
  expect(await found.findByText("Nothing matches this search.")).toBeTruthy();
  expect(found.queryAllByRole("heading", { level: 3 })).toEqual([]);
});

test("an empty box reads nothing at all", async () => {
  const { calls } = everything();
  await open();
  await type("  ");

  await waitFor(() => expect(screen.getByRole("searchbox", { name: "Search" })).toBeTruthy());
  expect(calls.some((url) => url.includes("q="))).toBe(false);
});
