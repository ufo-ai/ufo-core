import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";

import {
  AGENT,
  MEMBER,
  NO_TASKS,
  NO_TRIGGERS,
  TASK_KIND,
  TRIGGER_KIND,
  json,
  objectIndex,
  owned,
  useStreamFake,
  wire,
} from "./harness";

beforeEach(() => {
  useStreamFake();
});

test("the workspace Tasks tab draws both of its listings", async () => {
  wire({
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, []),
    "/objects/source_trigger": () => objectIndex(TRIGGER_KIND, []),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/workspace/tasks";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByRole("heading", { name: "Tasks" })).toBeTruthy();
  expect(await screen.findByText(NO_TASKS)).toBeTruthy();
  expect(await screen.findByText(NO_TRIGGERS)).toBeTruthy();
  // This screen is the one that writes a task: it holds every app's, and asks which app runs it.
  expect(screen.getByRole("button", { name: "New scheduled task" })).toBeTruthy();
});

test("a task the address opens stands in the sheet, where its pause is one press", async () => {
  const posted: unknown[] = [];
  wire({
    "/objects/scheduled_task/nightly-deploy": () =>
      json({
        ...TASK_KIND,
        name: "nightly-deploy",
        summary: "0 9 * * * — build the nightly",
        spec: { schedule: "0 9 * * *", prompt: "build the nightly", paused: false },
        status: { next_run_at: "2026-08-27T09:00:00+00:00", paused: false },
        links: [],
        created_at: "2026-08-01T09:00:00Z",
        updated_at: "2026-08-01T09:00:00Z",
      }),
    "/objects/scheduled_task": () =>
      objectIndex(TASK_KIND, [
        owned({ name: "nightly-deploy", summary: "0 9 * * * — build the nightly", paused: false }),
      ]),
    "/objects/source_trigger": () => objectIndex(TRIGGER_KIND, []),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Applied." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash =
    "#/workspace/tasks?open=object/" + AGENT.id + "/scheduled_task/nightly-deploy";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const sheet = await screen.findByRole("dialog", { name: "nightly-deploy" });
  await userEvent.click(within(sheet).getByRole("button", { name: "Pause" }));
  await vi.waitFor(() =>
    expect(posted).toEqual([
      {
        verb: "apply",
        kind: "scheduled_task",
        name: "nightly-deploy",
        spec: { paused: true },
      },
    ]),
  );
});
