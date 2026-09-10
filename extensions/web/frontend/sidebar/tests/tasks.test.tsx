import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { TASKS_HASH, chatHash } from "@/lib/route";

import {
  AGENT,
  CHAT_ROW,
  CONVO_ID,
  MEMBER,
  NO_TASKS,
  NO_TRIGGERS,
  TASK_KIND,
  TRIGGER_KIND,
  chatsOnWire,
  json,
  objectIndex,
  owned,
  useStreamFake,
  wire,
} from "./harness";

const THREADS_READ = "/objects/scheduled_task?order_by=last_run_at&order=desc";
const BOUND = "The threads that ran most recently. Task settings holds the rest.";

const THREAD_TASK = owned({
  name: "nightly-deploy",
  summary: "0 9 * * * — build the nightly",
  conversation: CONVO_ID,
  last_run_at: "2026-08-26T09:00:00+00:00",
  paused: false,
});

function taskThreadsWire(next: string | null = null) {
  return wire({
    ...chatsOnWire([CHAT_ROW]),
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, [THREAD_TASK], next),
    "/objects/source_trigger": () => objectIndex(TRIGGER_KIND, []),
    "/transcript": () => json({ messages: [] }),
  });
}

beforeEach(() => {
  location.hash = "";
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
  expect(screen.getByRole("button", { name: "New scheduled task" })).toBeTruthy();
});

test("the sidebar Tasks row opens the threads the tasks report into, and a row opens its chat", async () => {
  taskThreadsWire();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Tasks" }));

  expect(location.hash).toBe(TASKS_HASH);
  expect(await screen.findByRole("heading", { name: "Tasks" })).toBeTruthy();
  const page = screen.getByRole("main");
  const thread = await within(page).findByRole("button", { name: /Pick one thread/ });
  expect(within(page).queryByText(BOUND)).toBeNull();

  await userEvent.click(thread);
  expect(location.hash).toBe(chatHash(CONVO_ID));
});

test("the threads are read on the moment they are ranked by, and a cut page says so", async () => {
  const { calls } = taskThreadsWire("walk-on");
  location.hash = TASKS_HASH;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByRole("heading", { name: "Tasks" })).toBeTruthy();
  const page = screen.getByRole("main");
  expect(await within(page).findByText(BOUND)).toBeTruthy();
  expect(calls.some((url) => url.includes(THREADS_READ))).toBe(true);
});

test("the gear on the tasks threads opens the tasks themselves", async () => {
  taskThreadsWire();
  location.hash = TASKS_HASH;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Task settings" }));

  expect(location.hash).toBe("#/workspace/tasks");
  expect(await screen.findByText(NO_TRIGGERS)).toBeTruthy();
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
