import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { tasksHash } from "@/lib/route";

import {
  AGENT,
  CHAT_ROW,
  CONVO_ID,
  StreamFake,
  TURN_ID,
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

beforeEach(() => {
  location.hash = "";
  useStreamFake();
});

test("the tabs stand over the tasks screen, each drawing its own listing", async () => {
  wire({
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, []),
    "/objects/source_trigger": () => objectIndex(TRIGGER_KIND, []),
    "/objects/turn": () => json({ ...TURN_KIND, objects: [], next_cursor: null }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = tasksHash("scheduled");
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByRole("heading", { name: "Scheduled" })).toBeTruthy();
  const tabs = screen.getByRole("tablist", { name: "tasks" });
  expect(within(tabs).getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
    "Runs",
    "Scheduled",
    "Triggers",
  ]);
  expect(await screen.findByText(NO_TASKS)).toBeTruthy();
  expect(screen.queryByText(NO_TRIGGERS)).toBeNull();
  expect(screen.getByRole("button", { name: "New scheduled task" })).toBeTruthy();

  await userEvent.click(within(tabs).getByRole("tab", { name: "Triggers" }));

  expect(location.hash).toBe(tasksHash("triggers"));
  expect(await screen.findByText(NO_TRIGGERS)).toBeTruthy();
  expect(screen.queryByText(NO_TASKS)).toBeNull();
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
  location.hash = tasksHash("scheduled", {
    opens: ["object/" + AGENT.id + "/scheduled_task/nightly-deploy"],
  });
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

const TURN_KIND = {
  kind: "turn",
  fields: [
    "conversation",
    "agent_id",
    "status",
    "admission",
    "fired",
    "source",
    "source_name",
    "title",
    "created_at",
    "surface",
    "origin",
    "text",
  ],
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

const NIGHTLY_DETAIL = {
  ...TASK_KIND,
  name: "nightly-digest",
  summary: "0 9 * * * — the digest",
  spec: { schedule: "0 9 * * *", prompt: "digest the night", paused: false },
  status: { next_run_at: "2026-08-28T09:00:00+00:00", paused: false },
  links: [],
  created_at: "2026-08-01T09:00:00Z",
  updated_at: "2026-08-01T09:00:00Z",
};

function runsWire(transcript: unknown = { messages: [] }) {
  const runs = [firedTurn(TURN_ID, "running"), firedTurn(OLDER_RUN, "failed", "The feed 502ed.")];
  return wire({
    ...chatsOnWire([CHAT_ROW]),
    ["/objects/turn/" + TURN_ID]: () =>
      json({ ...TURN_KIND, ...runs[0], spec: null, status: runs[0], links: [] }),
    "/objects/turn": (url) =>
      json({
        ...TURN_KIND,
        objects: url.includes("source_name=nightly-digest") ? runs : runs,
        next_cursor: null,
      }),
    "/objects/scheduled_task/nightly-digest": () => json(NIGHTLY_DETAIL),
    "/objects/scheduled_task": () =>
      objectIndex(TASK_KIND, [
        owned({ name: "nightly-digest", summary: "0 9 * * * — the digest", paused: false }),
      ]),
    "/objects/source_trigger": () => objectIndex(TRIGGER_KIND, []),
    "/transcript": () => json(transcript),
    "/chat": () => json({ stopped: true }),
    "/intents": () => json({ applied: true, message: "Applied." }),
  });
}

test("the sidebar Tasks row lists the runs of scheduled work, newest first, marking the live one", async () => {
  const { calls } = runsWire();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Tasks" }));

  expect(location.hash).toBe(tasksHash("runs"));
  expect(await screen.findByRole("heading", { name: "Runs" })).toBeTruthy();
  const page = screen.getByRole("main");
  expect(await within(page).findByRole("img", { name: "Running" })).toBeTruthy();
  expect(within(page).getByRole("img", { name: "Failed" })).toBeTruthy();
  expect(within(page).getByText(/The feed 502ed\./)).toBeTruthy();
  const read = calls.find((url) => url.includes("/objects/turn?"));
  expect(read).toContain("fired=true");
  expect(read).toContain("order_by=created_at");
});

test("a run row opens its transcript read-only, and its Stop ends that run alone", async () => {
  const { handler } = runsWire({
    messages: [],
    turn: TURN_ID,
    turn_started_at: "2026-08-27T09:00:00Z",
  });
  location.hash = tasksHash("runs");
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const mark = await screen.findByRole("img", { name: "Running" });
  await userEvent.click(mark.closest('[role="button"]')!);

  expect(decodeURIComponent(location.hash)).toBe(tasksHash("runs") + "?open=run/" + TURN_ID);
  const drawer = await screen.findByRole("dialog", { name: "nightly-digest" });
  const stop = await within(drawer).findByRole("button", { name: "Stop" });
  expect(within(drawer).queryByRole("textbox")).toBeNull();

  await userEvent.click(stop);
  const stops = () =>
    handler.mock.calls.filter(
      ([, init]) => (init?.headers as Record<string, string>)?.["x-ufo-stop-turn"] === TURN_ID,
    );
  await waitFor(() => expect(stops()).toHaveLength(1));
  StreamFake.last().emit("terminal", { status: "cancelled" });
  await waitFor(() => expect(within(drawer).queryByRole("button", { name: "Stop" })).toBeNull());
});

test("a run's gear opens the settings of the task that fired it", async () => {
  runsWire();
  location.hash = tasksHash("runs");
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(
    (await screen.findAllByRole("button", { name: "Settings for nightly-digest" }))[0],
  );

  expect(decodeURIComponent(location.hash)).toContain("object/" + AGENT.id + "/scheduled_task/nightly-digest");
  const sheet = await screen.findByRole("dialog", { name: "nightly-digest" });
  expect(within(sheet).getByRole("button", { name: "Pause" })).toBeTruthy();
});

test("the filter narrows the runs to one task, and the narrowing rides the address", async () => {
  const { calls } = runsWire();
  location.hash = tasksHash("runs");
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByRole("img", { name: "Running" });
  await userEvent.click(screen.getByRole("button", { name: "Filter" }));
  await userEvent.click(await screen.findByRole("menuitemradio", { name: "nightly-digest" }));

  expect(decodeURIComponent(location.hash)).toBe(tasksHash("runs") + "?scope=scheduled_task/nightly-digest");
  await waitFor(() =>
    expect(calls.some((url) => url.includes("source_name=nightly-digest"))).toBe(true),
  );
});
