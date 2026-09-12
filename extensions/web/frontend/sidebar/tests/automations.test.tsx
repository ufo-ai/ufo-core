import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { automationsHash } from "@/lib/route";

import {
  AGENT,
  CHAT_ROW,
  CONVO_ID,
  TURN_ID,
  MEMBER,
  chatsOnWire,
  json,
  owned,
  automationsIndex,
  useStreamFake,
  wire,
} from "./harness";

beforeEach(() => {
  location.hash = "";
  useStreamFake();
});

const PULL_REQUEST = "https://github.com/metalcraftai/ufo/pull/3459";

const NIGHTLY = owned({
  kind: "scheduled_task",
  name: "nightly-digest",
  summary: "0 9 * * * — the digest",
  conversation: CONVO_ID,
  schedule: "0 9 * * *",
  description: "Digest the night's changes",
  prompt: "digest the night",
  next_run_at: "2026-08-28T09:00:00+00:00",
  last_run_at: "2026-08-27T09:00:00+00:00",
  last_run_status: "running",
  paused: false,
  origin: "Portal",
  mine: true,
});

const WATCHED_PULL = owned({
  kind: "source_trigger",
  name: "github-9f2c1a-4b8d21",
  summary: PULL_REQUEST + " on github (github account 9f2c1a)",
  conversation: CONVO_ID,
  connection: "github-9f2c1a",
  provider: "github",
  provider_label: "GitHub",
  resource: PULL_REQUEST,
  streams: "pull_requests",
  delivery: "current",
  last_run_at: "2026-08-26T18:00:00+00:00",
  origin: "Portal",
  mine: true,
});

const HEROES = [
  {
    mark: "kalyx",
    line: "Morning update from across my team at 9am.",
    ask: "Set up an automation that sends me a morning update from across my team at 9am.",
  },
];

const TURN_INDEX = {
  kind: "turn",
  fields: ["conversation", "agent_id", "status", "source", "source_name", "created_at", "text"],
  spec_schema: null,
  applies: false,
  deletes: false,
};

const OLDER_RUN = "66666666-6666-4666-8666-666666666666";

function firedTurn(name: string, status: string, text = "") {
  return owned({
    name,
    summary: "nightly-digest, " + status,
    conversation: CONVO_ID,
    agent_id: AGENT.id,
    status,
    admission: "scheduled",
    fired: true,
    source: "scheduled_task",
    source_name: "nightly-digest",
    title: "nightly-digest",
    created_at: name === TURN_ID ? "2026-08-27T09:00:00+00:00" : "2026-08-26T09:00:00+00:00",
    surface: "web",
    origin: "Portal",
    text,
  });
}

function automationsOnWire(transcript: unknown = { messages: [] }) {
  const runs = [firedTurn(TURN_ID, "running"), firedTurn(OLDER_RUN, "failed", "The feed 502ed.")];
  return wire({
    ...chatsOnWire([CHAT_ROW]),
    "/workspace/automations": () => json({ heroes: HEROES }),
    "/automations": () => automationsIndex([NIGHTLY, WATCHED_PULL]),
    ["/objects/turn/" + TURN_ID]: () =>
      json({ ...TURN_INDEX, name: TURN_ID, spec: null, status: runs[0], links: [] }),
    "/objects/turn": (url: string) =>
      json({
        ...TURN_INDEX,
        objects: url.includes("source_name=nightly-digest") ? runs : [],
        next_cursor: null,
      }),
    "/objects/scheduled_task/nightly-digest": () =>
      json({
        kind: "scheduled_task",
        fields: [],
        spec_schema: null,
        applies: true,
        deletes: true,
        name: "nightly-digest",
        summary: "0 9 * * * — the digest",
        spec: { schedule: "0 9 * * *", prompt: "digest the night", paused: false },
        status: { next_run_at: "2026-08-28T09:00:00+00:00", paused: false },
        links: [],
        created_at: "2026-08-01T09:00:00Z",
        updated_at: "2026-08-01T09:00:00Z",
      }),
    "/transcript": () => json(transcript),
    "/chat": () => json({ stopped: true }),
    "/intents": () => json({ applied: true, message: "Applied." }),
  });
}

test("the sidebar Automations row opens one table of scheduled tasks and triggers", async () => {
  automationsOnWire();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Automations" }));

  expect(location.hash).toBe(automationsHash());
  expect(await screen.findByRole("heading", { name: "Automations" })).toBeTruthy();
  expect(await screen.findByText("Digest the night's changes")).toBeTruthy();
  expect(screen.getByText("Watching " + PULL_REQUEST)).toBeTruthy();
});

test("the automation's Details carry its own fields, and a run there opens its transcript read-only", async () => {
  const { calls } = automationsOnWire({
    messages: [],
    turn: TURN_ID,
    turn_started_at: "2026-08-27T09:00:00Z",
  });
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByText("Digest the night's changes"));

  const pane = await screen.findByRole("dialog", { name: "Details" });
  expect(within(pane).getByLabelText("Name")).toHaveProperty("value", "Digest the night's changes");
  expect(within(pane).getByLabelText("Instructions")).toHaveProperty("value", "digest the night");
  expect(within(pane).getByRole("button", { name: "When to run" })).toBeTruthy();
  expect(within(pane).getByText("Run history")).toBeTruthy();
  expect(await within(pane).findByText(/The feed 502ed\./)).toBeTruthy();
  const read = calls.find((url) => url.includes("/objects/turn?"));
  expect(read).toContain("fired=true");
  expect(read).toContain("source_name=nightly-digest");

  await userEvent.click((await within(pane).findByRole("img", { name: "Running" })).closest("li")!);

  const run = await screen.findByRole("dialog", { name: "Run" });
  expect(await within(run).findByRole("button", { name: "Stop" })).toBeTruthy();
  expect(within(run).queryByRole("textbox")).toBeNull();
  expect(decodeURIComponent(location.hash)).toContain("run/" + AGENT.id + "/" + TURN_ID);
  expect(screen.queryByRole("dialog", { name: "Details" })).toBeNull();
});

test("a row carries no gear, so Details is the one way into an automation", async () => {
  automationsOnWire();
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByText("Digest the night's changes");
  expect(
    screen.queryByRole("button", { name: "Settings for Digest the night's changes" }),
  ).toBeNull();

  await userEvent.click(screen.getByText("Digest the night's changes"));
  const pane = await screen.findByRole("dialog", { name: "Details" });
  expect(within(pane).getByLabelText("Name")).toBeTruthy();
});
