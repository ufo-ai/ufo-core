import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { automationsHash } from "@/lib/route";

import {
  AGENT,
  CONVO_ID,
  MEMBER,
  json,
  owned,
  automationsIndex,
  useStreamFake,
  wire,
} from "../../tests/harness";

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

test("the sidebar Automations row opens one table of scheduled tasks and triggers", async () => {
  wire({
    "/workspace/automations": () => json({ heroes: HEROES }),
    "/automations": () => automationsIndex([NIGHTLY, WATCHED_PULL]),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Automations" }));

  expect(location.hash).toBe(automationsHash());
  expect(await screen.findByRole("heading", { name: "Automations" })).toBeTruthy();
  expect(await screen.findByText("Digest the night's changes")).toBeTruthy();
  expect(screen.getByText("Watching " + PULL_REQUEST)).toBeTruthy();
});
