import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { automationsHash, newChatHash } from "@/lib/route";

import {
  AGENT,
  ARRIVAL_ID,
  CONVO_ID,
  MEMBER,
  SECOND,
  TASK_KIND,
  TRIGGER_KIND,
  TURN_ID,
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

const TRIGGER_NAME = "github-9f2c1a-4b8d21";
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
  last_run_status: "done",
  paused: false,
  origin: "Portal",
  mine: true,
});

const PAUSED_TASK = owned({
  kind: "scheduled_task",
  name: "weekly-roundup",
  summary: "0 9 * * 1 — the roundup",
  conversation: CONVO_ID,
  schedule: "0 9 * * 1",
  description: "Round up the week",
  prompt: "round up the week",
  next_run_at: "2026-09-01T09:00:00+00:00",
  last_run_at: null,
  last_run_status: null,
  paused: true,
  origin: "Portal",
  mine: true,
});

const WATCHED_PULL = owned({
  kind: "source_trigger",
  name: TRIGGER_NAME,
  summary: PULL_REQUEST + " on github (github account 9f2c1a)",
  conversation: CONVO_ID,
  connection: "github-9f2c1a",
  provider: "github",
  provider_label: "GitHub",
  resource: PULL_REQUEST,
  streams: "pull_requests",
  delivery: "current",
  last_run_at: "2026-08-27T18:00:00+00:00",
  origin: "Portal",
  mine: true,
});

const WATCHED_FEED = owned({
  kind: "source_trigger",
  name: "github-9f2c1a-77aa10",
  summary: "github (github account 9f2c1a), pull_requests only",
  conversation: CONVO_ID,
  connection: "github-9f2c1a",
  provider: "github",
  provider_label: "GitHub",
  resource: "",
  streams: "pull_requests",
  delivery: "current",
  last_run_at: "2026-08-26T18:00:00+00:00",
  origin: "Portal",
  mine: true,
});

const RUN = owned({
  name: TURN_ID,
  summary: "nightly-digest, done",
  conversation: CONVO_ID,
  agent_id: AGENT.id,
  status: "done",
  admission: "scheduled",
  fired: true,
  source: "scheduled_task",
  source_name: "nightly-digest",
  title: "nightly-digest",
  created_at: "2026-08-27T09:00:00+00:00",
  surface: "web",
  origin: "Portal",
  text: "Nothing changed overnight.",
});

const OLDER_RUN = {
  ...RUN,
  name: ARRIVAL_ID,
  created_at: "2026-08-26T09:00:00+00:00",
  text: "The night before was quiet too.",
};

const RUNS_CURSOR = "older-runs";

const REFUSED_EDIT = "Only the member who wrote this task can change it.";

const HEROES = [
  {
    mark: "kalyx",
    title: "Deploy recap",
    line: "Recap yesterday's deploys every weekday at 9am.",
    ask: "Set up an automation that recaps yesterday's deploys every weekday at 9am.",
  },
];

const LONG_NAME =
  "Digest every change that landed on the nightly branch and everything it touched downstream";

const TURN_INDEX = {
  kind: "turn",
  fields: ["conversation", "agent_id", "status", "source", "source_name", "created_at", "text"],
  spec_schema: null,
  applies: false,
  deletes: false,
};

function automationsOnWire(
  rows: unknown[] = [WATCHED_PULL, NIGHTLY, WATCHED_FEED, PAUSED_TASK],
  posted: Record<string, unknown>[] = [],
  runsCursor: string | null = null,
) {
  let held = rows as Record<string, unknown>[];
  return wire({
    "/intents": (_url: string, init?: RequestInit) => {
      const envelope = JSON.parse(String(init?.body));
      posted.push(envelope);
      held = held.map((row) =>
        row.name === envelope.name ? { ...row, ...envelope.spec } : row,
      );
      return json({ applied: true, message: "Applied." });
    },
    "/workspace/automations": () => json({ heroes: HEROES }),
    "/automations": () => automationsIndex(held),
    ["/objects/turn/" + TURN_ID]: () =>
      json({ ...TURN_INDEX, name: TURN_ID, spec: null, status: RUN, links: [] }),
    ["/objects/turn/" + ARRIVAL_ID]: () =>
      json({ ...TURN_INDEX, name: ARRIVAL_ID, spec: null, status: OLDER_RUN, links: [] }),
    "/objects/turn": (url: string) => {
      if (!url.includes("source_name=nightly-digest"))
        return json({ ...TURN_INDEX, objects: [], next_cursor: null });
      if (url.includes("cursor=" + RUNS_CURSOR))
        return json({ ...TURN_INDEX, objects: [OLDER_RUN], next_cursor: null });
      return json({ ...TURN_INDEX, objects: [RUN], next_cursor: runsCursor });
    },
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
    "/transcript": () => json({ messages: [] }),
    "/chat": () => json({ stopped: true }),
  });
}

test("one table lists tasks and triggers, most recently run first, in the member's words", async () => {
  automationsOnWire();
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("Digest the night's changes")).toBeTruthy();
  const heads = screen.getAllByRole("columnheader").map((head) => head.textContent);
  expect(heads).toEqual(["Name", "Events", "Next run", "Last run"]);
  const names = screen
    .getAllByRole("row")
    .slice(1)
    .map((row) => within(row).getAllByRole("cell")[0]?.textContent);
  expect(names).toEqual([
    "Watching " + PULL_REQUEST,
    "Digest the night's changes",
    "Watching GitHub pull_requests",
    "Round up the week",
  ]);
  expect(screen.queryByText("nightly-digest")).toBeNull();
  expect(screen.queryByText(/github-9f2c1a/)).toBeNull();
  expect(screen.getByRole("img", { name: "Paused" })).toBeTruthy();
  expect(screen.getAllByRole("img", { name: "Schedule" })).toHaveLength(2);
  expect(screen.getAllByRole("img", { name: "GitHub pull_requests" })).toHaveLength(2);
});

test("a long automation name is cut to its column rather than stretching the table", async () => {
  automationsOnWire([{ ...NIGHTLY, description: LONG_NAME }]);
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const said = await screen.findByText(LONG_NAME);
  expect(said.className).toContain("truncate");
  const cell = said.closest("td")!;
  expect(cell.className).toContain("truncate");
  expect(cell.className).not.toContain("whitespace-nowrap");
  expect(screen.getAllByRole("columnheader")[0].className).not.toContain("w-(--size-fact-column)");
});

test("a task pauses, resumes and deletes from its own Details", async () => {
  const posted: Record<string, unknown>[] = [];
  automationsOnWire(undefined, posted);
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByText("Digest the night's changes"));
  const details = await screen.findByRole("dialog", { name: "Details" });

  await userEvent.click(within(details).getByRole("button", { name: "Pause" }));
  await vi.waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toEqual({
    verb: "apply",
    kind: "scheduled_task",
    name: "nightly-digest",
    spec: { paused: true },
  });
  await within(details).findByRole("button", { name: "Resume" });

  await userEvent.click(within(details).getByRole("button", { name: "Delete" }));
  await userEvent.click(within(details).getByRole("button", { name: "Confirm delete" }));

  await vi.waitFor(() => expect(posted.length).toBe(2));
  expect(posted[1]).toEqual({ verb: "delete", kind: "scheduled_task", name: "nightly-digest" });
  await vi.waitFor(() => expect(screen.queryByRole("dialog", { name: "Details" })).toBeNull());
});

test("a paused task offers Resume, and a trigger offers neither act", async () => {
  automationsOnWire();
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByText("Round up the week"));
  const paused = await screen.findByRole("dialog", { name: "Details" });
  expect(within(paused).getByRole("button", { name: "Resume" })).toBeTruthy();

  await userEvent.click(within(paused).getByRole("button", { name: "Close" }));
  await userEvent.click(await screen.findByText("Watching " + PULL_REQUEST));
  const watched = await screen.findByRole("dialog", { name: "Details" });
  expect(within(watched).queryByRole("button", { name: "Pause" })).toBeNull();
  expect(within(watched).queryByRole("button", { name: "Delete" })).toBeNull();
});

test("an automation with no run yet points at the conversation it reports into", async () => {
  automationsOnWire([PAUSED_TASK]);
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByText("Round up the week"));
  const details = await screen.findByRole("dialog", { name: "Details" });

  const link = await within(details).findByRole("link");
  expect(link.getAttribute("href")).toContain(CONVO_ID);
  expect(within(details).getByText(/No run yet/)).toBeTruthy();
});

test("the list draws no run, so a run is reached through the automation it ran for", async () => {
  automationsOnWire();
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("Digest the night's changes")).toBeTruthy();
  expect(screen.queryByText(/Nothing changed overnight/)).toBeNull();
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("an automation opens Details: the fields it runs under, with its runs under them", async () => {
  automationsOnWire();
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByText("Digest the night's changes"));

  const details = await screen.findByRole("dialog", { name: "Details" });
  expect(decodeURIComponent(location.hash)).toBe(
    automationsHash() + "?open=automation/" + AGENT.id + "/scheduled_task/nightly-digest",
  );
  expect(within(details).getByLabelText("Name")).toHaveProperty(
    "value",
    "Digest the night's changes",
  );
  const instructions = within(details).getByLabelText("Instructions");
  expect(instructions.tagName).toBe("TEXTAREA");
  expect(instructions).toHaveProperty("value", "digest the night");
  expect(within(details).getByText("When to run")).toBeTruthy();
  expect(within(details).getByText("Run history")).toBeTruthy();
  expect(await within(details).findByText(/Nothing changed overnight/)).toBeTruthy();
  expect(within(details).queryByRole("button", { name: "Older" })).toBeNull();
  expect(within(details).queryByRole("heading", { name: "Digest the night's changes" })).toBeNull();
});

test("the instructions entry stands a few lines and grows on the press that expands it", async () => {
  automationsOnWire();
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByText("Digest the night's changes"));
  const details = await screen.findByRole("dialog", { name: "Details" });
  const box = within(details).getByLabelText("Instructions") as HTMLTextAreaElement;
  const collapsed = box.rows;

  await userEvent.click(within(details).getByRole("button", { name: "Expand" }));
  expect(box.rows).toBeGreaterThan(collapsed);

  await userEvent.click(within(details).getByRole("button", { name: "Collapse" }));
  expect(box.rows).toBe(collapsed);
});

test("the name entry saves itself onto the task", async () => {
  const posted: Record<string, unknown>[] = [];
  automationsOnWire(undefined, posted);
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByText("Digest the night's changes"));
  const details = await screen.findByRole("dialog", { name: "Details" });
  const name = within(details).getByLabelText("Name");
  fireEvent.change(name, { target: { value: "Digest the night" } });
  fireEvent.blur(name);

  await vi.waitFor(() =>
    expect(posted).toEqual([
      {
        verb: "apply",
        kind: "scheduled_task",
        name: "nightly-digest",
        spec: { description: "Digest the night" },
      },
    ]),
  );
});

test("the frequency picked in Details is written as the task's own cron", async () => {
  const posted: Record<string, unknown>[] = [];
  automationsOnWire(undefined, posted);
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByText("Digest the night's changes"));
  const details = await screen.findByRole("dialog", { name: "Details" });
  expect(within(details).getByRole("button", { name: "When to run" })).toHaveProperty(
    "textContent",
    "Every day",
  );

  await userEvent.click(within(details).getByRole("button", { name: "When to run" }));
  const menu = await screen.findByRole("menu");
  expect([...menu.querySelectorAll("[role=menuitemradio]")].map((one) => one.textContent)).toEqual([
    "Every hour",
    "Every day",
    "Every weekday",
    "Every week",
    "Every month",
  ]);

  await userEvent.click(within(menu).getByRole("menuitemradio", { name: "Every weekday" }));

  await vi.waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({
    verb: "apply",
    kind: "scheduled_task",
    name: "nightly-digest",
    spec: { schedule: "0 9 * * 1,2,3,4,5" },
  });
});

test("a monthly frequency is picked as a start date and fires on that day of the month", async () => {
  const posted: Record<string, unknown>[] = [];
  automationsOnWire(undefined, posted);
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByText("Digest the night's changes"));
  const details = await screen.findByRole("dialog", { name: "Details" });
  await userEvent.click(within(details).getByRole("button", { name: "When to run" }));
  await userEvent.click(
    within(await screen.findByRole("menu")).getByRole("menuitemradio", { name: "Every month" }),
  );

  await vi.waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({ spec: { schedule: "0 9 1 * *" } });

  const date = (await within(details).findByLabelText("Starting")) as HTMLInputElement;
  expect(date.value.endsWith("-01")).toBe(true);
  fireEvent.change(date, { target: { value: "2026-09-12" } });

  await vi.waitFor(() => expect(posted.length).toBe(2));
  expect(posted[1]).toMatchObject({ spec: { schedule: "0 9 12 * *" } });
});

test("a run listed in Details opens its transcript read-only, beside the automation it ran for", async () => {
  automationsOnWire();
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByText("Digest the night's changes"));
  const details = await screen.findByRole("dialog", { name: "Details" });
  await userEvent.click((await within(details).findByRole("img", { name: "Done" })).closest("li")!);

  const run = await screen.findByRole("dialog", { name: "Run" });
  expect(await within(run).findByText("No messages in this conversation yet.")).toBeTruthy();
  expect(within(run).queryByRole("textbox")).toBeNull();
  expect(within(run).queryByRole("button", { name: "Send" })).toBeNull();
  expect(decodeURIComponent(location.hash)).toBe(
    automationsHash() +
      "?open=" +
      [
        "automation/" + AGENT.id + "/scheduled_task/nightly-digest",
        "run/" + AGENT.id + "/" + TURN_ID,
      ].join("~"),
  );
});

test("the run history pages on its own cursor, and the automations list stays where it stands", async () => {
  const { calls } = automationsOnWire(undefined, [], RUNS_CURSOR);
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByText("Digest the night's changes"));
  const details = await screen.findByRole("dialog", { name: "Details" });
  expect(await within(details).findByText(/Nothing changed overnight/)).toBeTruthy();

  await userEvent.click(await within(details).findByRole("button", { name: "Older" }));

  expect(await within(details).findByText(/The night before was quiet too/)).toBeTruthy();
  expect(decodeURIComponent(location.hash)).toContain("runs=" + RUNS_CURSOR);
  expect(
    calls.some((url) => url.includes("/objects/turn?") && url.includes("cursor=" + RUNS_CURSOR)),
  ).toBe(true);
  expect(calls.filter((url) => url.includes("/automations?") && url.includes("cursor="))).toEqual(
    [],
  );
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
  const details = await screen.findByRole("dialog", { name: "Details" });
  expect(within(details).getByLabelText("Name")).toBeTruthy();
});

test("a task another member wrote states the refusal and stands its cadence disabled", async () => {
  const theirs = { ...NIGHTLY, mine: false };
  wire({
    "/intents": () => json({ applied: false, message: REFUSED_EDIT }),
    "/workspace/automations": () => json({ heroes: HEROES }),
    "/automations": () => automationsIndex([theirs]),
    "/objects/turn": () => json({ ...TURN_INDEX, objects: [], next_cursor: null }),
  });
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByText("Digest the night's changes"));
  const details = await screen.findByRole("dialog", { name: "Details" });

  expect(within(details).getByRole("button", { name: "When to run" })).toHaveProperty(
    "disabled",
    true,
  );
  expect(within(details).getByLabelText("Name")).toHaveProperty("readOnly", true);
  expect(within(details).getByRole("button", { name: "Pause" })).toHaveProperty("disabled", true);
  expect(within(details).getByRole("button", { name: "Delete" })).toHaveProperty("disabled", true);

  fireEvent.change(within(details).getByLabelText("Name"), { target: { value: "Theirs" } });
  fireEvent.blur(within(details).getByLabelText("Name"));

  expect(
    await within(details).findByText(REFUSED_EDIT),
  ).toBeTruthy();
});

test("the New automation act is the header's filled pill, marked with a plus", async () => {
  automationsOnWire();
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const act = await screen.findByRole("button", { name: "New automation" });
  expect(act.className).toContain("bg-ink");
  expect(act.className).toContain("text-surface");
  expect(act.className).toContain("rounded-full");
  expect(act.querySelector("svg")?.classList.contains("tabler-icon-plus")).toBe(true);
});

/** `held` is every name the kind holds, which is not what the page in front of the member lists:
 *  the listing pages, and a name off the current page is the one a create can overwrite. */
function creationOnWire(
  rows: unknown[],
  posted: Record<string, unknown>[],
  held: string[] = rows.map((row) => (row as { name: string }).name),
) {
  wire({
    "/intents": (_url: string, init?: RequestInit) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Applied." });
    },
    ["/objects/" + TASK_KIND.kind + "/"]: (url: string) => {
      const asked = url.split("/objects/" + TASK_KIND.kind + "/")[1];
      const name = decodeURIComponent(asked.split("?")[0]);
      return held.includes(name)
        ? json({ ...TASK_KIND, name, spec: {}, status: {}, links: [] })
        : new Response("no such object", { status: 404 });
    },
    "/workspace/automations": () => json({ heroes: HEROES }),
    "/automations": () =>
      json({ kinds: [TASK_KIND, TRIGGER_KIND], objects: rows, next_cursor: null }),
    "/objects/turn": () => json({ ...TURN_INDEX, objects: [], next_cursor: null }),
  });
}

async function describedAutomation(description: string): Promise<HTMLElement> {
  await userEvent.click(await screen.findByRole("button", { name: "New automation" }));
  const pane = await screen.findByRole("dialog", { name: "New automation" });
  await userEvent.type(within(pane).getByLabelText("Name"), description);
  await userEvent.type(within(pane).getByLabelText("Instructions"), "digest the night");
  return pane;
}

test("the New automation pane edits what Details edits, and nothing the old form asked for", async () => {
  creationOnWire([NIGHTLY], []);
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "New automation" }));
  const pane = await screen.findByRole("dialog", { name: "New automation" });
  expect(within(pane).getByLabelText("Name").tagName).toBe("INPUT");
  expect(within(pane).getByLabelText("Instructions").tagName).toBe("TEXTAREA");
  expect(within(pane).getByText("When to run")).toBeTruthy();
  expect(within(pane).queryByLabelText("Description")).toBeNull();
  expect(within(pane).queryByLabelText("Prompt")).toBeNull();
  expect(within(pane).queryByLabelText("Schedule")).toBeNull();
});

test("the New automation pane asks nothing about apps, whatever the workspace holds", async () => {
  creationOnWire([NIGHTLY], []);
  location.hash = automationsHash();
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "New automation" }));
  const pane = await screen.findByRole("dialog", { name: "New automation" });
  expect(within(pane).queryByLabelText("App")).toBeNull();
  expect(within(pane).queryByRole("combobox")).toBeNull();
});

test("a new automation is filed under the name it is given, slugged", async () => {
  const posted: Record<string, unknown>[] = [];
  creationOnWire([NIGHTLY], posted);
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const pane = await describedAutomation("Weekday morning engineering digest!");
  await userEvent.click(within(pane).getByRole("button", { name: "Create" }));

  await vi.waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({
    verb: "apply",
    kind: "scheduled_task",
    name: "weekday-morning-engineering-digest",
    spec: {
      description: "Weekday morning engineering digest!",
      prompt: "digest the night",
      schedule: "0 9 * * *",
    },
  });
});

test("the frequency picked on the new automation is written as its cron", async () => {
  const posted: Record<string, unknown>[] = [];
  creationOnWire([NIGHTLY], posted);
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const pane = await describedAutomation("Weekday morning engineering digest");
  expect(within(pane).getByRole("button", { name: "When to run" })).toHaveProperty(
    "textContent",
    "Every day",
  );

  await userEvent.click(within(pane).getByRole("button", { name: "When to run" }));
  await userEvent.click(
    within(await screen.findByRole("menu")).getByRole("menuitemradio", { name: "Every weekday" }),
  );
  expect(posted).toEqual([]);

  await userEvent.click(within(pane).getByRole("button", { name: "Create" }));

  await vi.waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({ spec: { schedule: "0 9 * * 1,2,3,4,5" } });
});

test("a name that slugs onto a listed automation is filed under a counted name", async () => {
  const posted: Record<string, unknown>[] = [];
  creationOnWire([{ ...NIGHTLY, name: "weekday-morning-engineering-digest" }], posted);
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const pane = await describedAutomation("Weekday morning engineering digest");
  await userEvent.click(within(pane).getByRole("button", { name: "Create" }));

  await vi.waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({ name: "weekday-morning-engineering-digest-2" });
});

test("a name another page holds is counted rather than written over", async () => {
  const posted: Record<string, unknown>[] = [];
  creationOnWire([NIGHTLY], posted, ["weekday-morning-engineering-digest"]);
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const pane = await describedAutomation("Weekday morning engineering digest");
  await userEvent.click(within(pane).getByRole("button", { name: "Create" }));

  await vi.waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({ name: "weekday-morning-engineering-digest-2" });
});

test("a name longer than an object name allows is filed under a cut slug", async () => {
  const posted: Record<string, unknown>[] = [];
  creationOnWire([NIGHTLY], posted);
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const pane = await describedAutomation(
    "Daily meeting brief to get me ready for the day and list my actions",
  );
  await userEvent.click(within(pane).getByRole("button", { name: "Create" }));

  await vi.waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({
    name: "daily-meeting-brief-to-get-me-ready-for-the-day-and-list-my-acti",
  });
  expect(String(posted[0].name)).toHaveLength(64);
});

test("a member holding no automation reads the suggestions alone", async () => {
  automationsOnWire([]);
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByRole("heading", { name: "Explore automations" })).toBeTruthy();
  expect(screen.getByText("Put your recurring work on autopilot.")).toBeTruthy();
  expect(screen.getByText(HEROES[0].line)).toBeTruthy();
  expect(screen.getByRole("button", { name: "New automation" })).toBeTruthy();
});

test("the suggestions head the list once the member holds automations", async () => {
  automationsOnWire();
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText(HEROES[0].line)).toBeTruthy();
  const drawn = [...document.querySelectorAll("[data-part=body], [data-slot=table-cell]")].map(
    (part) => part.textContent,
  );
  expect(drawn[0]).toBe(HEROES[0].line);
  expect(drawn).toContain("Digest the night's changes");
});

test("a suggestion card is drawn as a connection card is: mark, title, line, and Create", async () => {
  automationsOnWire([]);
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const card = (await screen.findByText(HEROES[0].line)).closest("li")!;
  expect(card.className).toContain("rounded-panel");
  expect(card.className).toContain("border-edge");
  const tile = card.querySelector("[data-part=mark]")!;
  expect(tile.className).toContain("rounded-panel");
  expect(tile.className).toContain("bg-fill");
  expect(tile.querySelector("svg")).toBeTruthy();
  expect(card.querySelector("[data-part=primary]")?.textContent).toBe(HEROES[0].title);
  expect(card.querySelector("[data-part=body]")?.textContent).toBe(HEROES[0].line);
  const act = within(card).getByRole("button", { name: "Create" });
  expect(act.className).toContain("bg-ink");
  expect(act.className).toContain("rounded-full");
});

test("the Create act on a suggestion opens a new chat with the main agent", async () => {
  automationsOnWire([]);
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Create" }));

  await vi.waitFor(() => expect(location.hash).toBe(newChatHash(AGENT.id)));
});

test("a suggestion opens a new chat with the main agent", async () => {
  automationsOnWire([]);
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click((await screen.findByText(HEROES[0].line)).closest("li")!);

  await vi.waitFor(() => expect(location.hash).toBe(newChatHash(AGENT.id)));
});

test("a scheduled task states its next run and a trigger leaves that column blank", async () => {
  automationsOnWire();
  location.hash = automationsHash();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const cells = async (title: string) => {
    const row = (await screen.findByText(title)).closest("tr")!;
    return within(row)
      .getAllByRole("cell")
      .map((cell) => cell.textContent);
  };

  expect((await cells("Digest the night's changes"))[2]).toBeTruthy();
  expect((await cells("Watching " + PULL_REQUEST))[2]).toBe("");
  expect((await cells("Round up the week"))[2]).toBe("");
});
