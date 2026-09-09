import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { chatHash } from "@/lib/route";

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
  pressRow,
  useStreamFake,
  wire,
} from "./harness";

beforeEach(() => {
  useStreamFake();
});

const CONVERSATION_ID = "d28e2f45-85c5-4ce8-bc12-32b42b32af91";

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
  expect(screen.getByRole("button", { name: "New scheduled task" })).toBeTruthy();
});

test("a task the address opens links its conversation and offers pause", async () => {
  const posted: unknown[] = [];
  wire({
    "/objects/scheduled_task/nightly-deploy": () =>
      json({
        ...TASK_KIND,
        name: "nightly-deploy",
        summary: "0 9 * * * — build the nightly",
        spec: { schedule: "0 9 * * *", prompt: "build the nightly", paused: false },
        status: {
          conversation: CONVERSATION_ID,
          next_run_at: "2026-08-27T09:00:00+00:00",
          paused: false,
        },
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
  expect(within(sheet).getByRole("link", { name: "Conversation" }).getAttribute("href")).toBe(
    chatHash(CONVERSATION_ID),
  );
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

  await userEvent.click(within(sheet).getByRole("link", { name: "Conversation" }));
  expect(location.hash).toBe(chatHash(CONVERSATION_ID));
});

test.each([
  { action: "Close", remembered: false },
  { action: "Escape", remembered: false },
  { action: "Close", remembered: true },
  { action: "Escape", remembered: true },
])("$action clears a task drawer when remembered is $remembered", async ({ action, remembered }) => {
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
    "/workspace/team": () => json({ members: [], can_manage: false, actions: [] }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/workspace/tasks";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await pressRow("nightly-deploy");
  await screen.findByRole("dialog", { name: "nightly-deploy" });
  if (remembered) {
    await userEvent.click(screen.getByRole("tab", { name: "Team" }));
    await userEvent.click(await screen.findByRole("tab", { name: "Tasks" }));
  }

  const sheet = await screen.findByRole("dialog", { name: "nightly-deploy" });
  if (action === "Escape") await userEvent.keyboard("{Escape}");
  else await userEvent.click(within(sheet).getByRole("button", { name: "Close" }));

  await vi.waitFor(() =>
    expect(screen.queryByRole("dialog", { name: "nightly-deploy" })).toBeNull(),
  );
  expect(location.hash).toBe(remembered ? "#/workspace/tasks?open=" : "#/workspace/tasks");

  await userEvent.click(screen.getByRole("tab", { name: "Team" }));
  await userEvent.click(await screen.findByRole("tab", { name: "Tasks" }));

  expect(screen.queryByRole("dialog", { name: "nightly-deploy" })).toBeNull();
  expect(location.hash).toBe("#/workspace/tasks");
});
