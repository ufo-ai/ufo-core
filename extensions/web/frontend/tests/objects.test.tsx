import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { ObjectPane } from "@/kernel/objects";
import type { Placement } from "@/kernel/pager";
import { Viewer } from "@/lib/audience";
import { relativeMoment } from "@/lib/moments";
import { MainAgentProvider } from "@/lib/mainAgent";
import { Radar } from "@/views/Radar";

import {
  AGENT,
  AGENT_ID,
  CONVO_ID,
  MEMBER,
  NO_ARTIFACTS,
  NO_RUNS,
  NO_TASKS,
  NO_TRIGGERS,
  SECOND,
  SECOND_ID,
  SITE_KIND,
  TASK_KIND,
  TRIGGER_KIND,
  fact,
  json,
  objectIndex,
  pageFits,
  owned,
  pick,
  openRow,
  refusedNotice,
  useStreamFake,
  viewCard,
  wire,
} from "./harness";

const NOW = new Date("2026-08-01T12:00:00Z");
const IN_THREE_HOURS = new Date(Date.now() + 3 * 3_600_000).toISOString();

const LAST_RUN = "2026-07-31T09:00:00Z";

const TASK_ROW = owned({
  name: "daily-brief",
  summary: "0 9 * * * — daily brief",
  conversation: CONVO_ID,
  mine: true,
  next_run_at: IN_THREE_HOURS,
  last_run_at: LAST_RUN,
  last_run_status: "done",
  origin: "#general",
  prompt: "write the daily brief",
  paused: false,
  owner_email: "mel@example.com",
});

/** A prompt of the length a member actually writes one: several steps over several lines. */
const LONG_PROMPT = [
  "1. Read every pull request merged in the last 24 hours.",
  "2. Group them by the surface each one lands on.",
  "3. Post the roll-up to the channel this task reports to.",
].join("\n");

const LONG_URL =
  "https://app.testing.flyingobject.ai/surface/web/objects/scheduled_task/" +
  "daily-ufo-changelog?agent=d5eeb0ec-8aa7-4624-be37-1411f2d13b54";

const SECOND_CONVO_ID = "6f1d4c2a-9b3e-4a71-8c05-2d7e6b1f0a94";

const SECOND_TASK_ROW = owned(
  {
    name: "weekly-roll",
    summary: "0 9 * * 1 — weekly roll-up",
    conversation: SECOND_CONVO_ID,
    next_run_at: IN_THREE_HOURS,
    last_run_at: null,
    last_run_status: null,
    paused: false,
  },
  SECOND,
);

const RUN = {
  turn_id: "0b7e2d43-5a86-4f19-9c3d-8e64a02b7c15",
  conversation_id: CONVO_ID,
  agent_id: AGENT_ID,
  fired_at: "2026-08-14T09:00:00+00:00",
  status: "done",
  task: "daily-brief",
  surface: "web",
  source: null,
  text: "",
  artifacts: [
    {
      filename: "roll-up.pdf",
      subject: null,
      media_type: "application/pdf",
      size_bytes: 5,
      url: "/dl/roll-up.pdf",
      preview_url: null,
    },
  ],
};

const TASK_DETAIL = {
  ...TASK_KIND,
  name: "daily-brief",
  summary: "0 9 * * * — daily brief",
  spec: { schedule: "0 9 * * *", prompt: "write the daily brief", paused: false },
  status: { next_run_at: IN_THREE_HOURS, paused: false, owner_email: "mel@example.com" },
  links: [{ relation: "reports_to", kind: "conversation", name: CONVO_ID, opens: true }],
  created_at: "2026-07-01T09:00:00Z",
  updated_at: "2026-07-02T09:00:00Z",
};

const CONVERSATION_DETAIL = {
  kind: "conversation",
  fields: ["surface", "surface_label"],
  spec_schema: {
    properties: {
      surface: { type: "string" },
      surface_label: { type: "string" },
      audience: { type: "string" },
    },
  },
  applies: false,
  name: CONVO_ID,
  summary: "web conversation, created 2026-07-01",
  spec: { surface: "web", surface_label: null, audience: "member:m1" },
  status: { surface: "web" },
  links: [{ relation: "scoped_to", kind: "agent", name: "assistant", opens: false }],
  created_at: "2026-07-01T09:00:00Z",
  updated_at: null,
};

beforeEach(() => {
  useStreamFake();
});

function cells(name: string): string[] {
  const row = screen.getByText(name).closest("tr");
  return [...(row?.querySelectorAll("td") ?? [])].map((box) => String(box.textContent));
}

function headings(): string[] {
  return screen.getAllByRole("columnheader").map((head) => String(head.textContent));
}

/** One fact of the detail's `Spec` group, scoped to that group: `Status` states some of the same
 *  fields, so a label alone names two rows on the page. */
function specFact(label: string): { row: HTMLElement; said: HTMLElement } {
  const group = [...document.querySelectorAll("h2")]
    .find((heading) => heading.textContent === "Spec")
    ?.closest("section");
  const term = [...(group?.querySelectorAll("dt") ?? [])].find(
    (candidate) => candidate.textContent === label,
  );
  if (!term?.parentElement || !term.nextElementSibling) {
    throw new Error("the Spec group states no fact " + label);
  }
  return { row: term.parentElement, said: term.nextElementSibling as HTMLElement };
}

function mount(agents = [AGENT]) {
  render(
    <MainAgentProvider agents={agents}>
      <ObjectPane agentId={null} kind="scheduled_task" />
    </MainAgentProvider>,
  );
}

function mountAgent() {
  render(
    <MainAgentProvider agents={[AGENT, SECOND]}>
      <ObjectPane agentId={AGENT_ID} kind="scheduled_task" />
    </MainAgentProvider>,
  );
}

function PlacedRadar() {
  const [place, setPlace] = useState<Placement>({});
  return (
    <Radar
      agentId={null}
      title="Radar"
      place={place}
      onPlace={(patch) => setPlace((held) => ({ ...held, ...patch }))}
    />
  );
}

function mountRadar() {
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedRadar />
    </MainAgentProvider>,
  );
}

test("a moment reads as elapsed behind now and as remaining ahead of it", () => {
  expect(relativeMoment("2026-08-01T11:59:30Z", NOW)).toBe("now");
  expect(relativeMoment("2026-08-01T11:30:00Z", NOW)).toBe("30m ago");
  expect(relativeMoment("2026-07-29T12:00:00Z", NOW)).toBe("3d ago");
  expect(relativeMoment("2026-08-01T15:00:00Z", NOW)).toBe("in 3h");
  expect(relativeMoment("not a moment", NOW)).toBe("not a moment");
});

/** The prompt is not a column: every row cut it mid-word, so the kind carries no prose at all and
 *  the width goes to the two runs instead — the one behind the task with how it ended, and the one
 *  ahead of it. */
test("an index carries the name and the two facts a prose-less kind leads with", async () => {
  wire({ "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW]) });
  mount();

  const name = await screen.findByText("daily-brief");
  const row = name.closest("tr");
  expect(row).not.toBeNull();
  expect(headings()).toEqual(["Name", "Agent", "Last Run At", "Next Run At", ""]);
  const said = cells("daily-brief");
  expect(said[0]).toBe("daily-briefActive");
  expect(said[1]).toBe("assistant");
  expect(said[2]).toBe("Jul 31 2026 · done");
  expect(said[3]).toContain("in ");
  expect(screen.queryByText("write the daily brief")).toBeNull();
  expect(screen.queryByText("0 9 * * * — daily brief")).toBeNull();
  expect(screen.queryByText("#general")).toBeNull();
  const rowCells = [...(row?.querySelectorAll("td") ?? [])];
  expect(rowCells[1].className).toContain("w-(--size-fact-column)");
  expect(rowCells[2].className).toContain("w-(--size-fact-column)");
  expect(rowCells[2].querySelector("span")?.className).toContain("block truncate");
  expect(rowCells[3].className).toContain("w-(--size-fact-column)");
  expect(rowCells[3].querySelector("span")?.className).toContain("block truncate");
});

/** Paused stood only as a filter above the table, so "what is up with this one" took a press into
 *  every row. It reads on the row now, and it still costs no column. */
test("whether a task is stopped reads beside its own name", async () => {
  wire({
    "/objects/scheduled_task": () =>
      objectIndex(TASK_KIND, [TASK_ROW, owned({ ...TASK_ROW, name: "held", paused: true })]),
  });
  mount();

  await screen.findByText("daily-brief");
  expect(headings()).not.toContain("Paused");
  expect(cells("daily-brief")[0]).toBe("daily-briefActive");
  expect(cells("held")[0]).toBe("heldPaused");
  const chip = [...(screen.getByText("held").closest("tr")?.querySelectorAll("span") ?? [])].find(
    (span) => span.textContent === "Paused",
  );
  expect(chip?.className).toContain("rounded-control");
  expect(screen.getByRole("tab", { name: "Paused" })).toBeTruthy();
});

/** The tracks are fixed pixels, so a table whose tracks outrun the page it is read on holds its
 *  width and scrolls the column sideways — and what falls off the right is the act the row is
 *  pressed by. The desktop the portal is read at has to clear the sum, sidebar and gutters included,
 *  or every member scrolls to reach `Open`. It measures the pane a list has to itself; a record
 *  opened beside one takes its own column out of that width, and a list read under one is narrower
 *  than any floor. */
test("an index fits the desktop it is read on, so the row's act never scrolls off", async () => {
  wire({ "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW]) });
  mount();

  await screen.findByText("daily-brief");
  const across = screen.getByRole("table").style.minWidth;
  expect(pageFits(across)).toBe(true);
  expect(across).toBe(
    "calc(3 * var(--size-fact-column) + 1 * var(--size-prose-column) + 1 * var(--size-act))",
  );
  expect(
    pageFits("calc(1 * var(--size-fact-column) + 4 * var(--size-prose-column) + 1 * var(--size-act))"),
  ).toBe(false);
});

test("one agent's index fits that same desktop", async () => {
  wire({ "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW]) });
  mountAgent();

  await screen.findByText("daily-brief");
  const across = screen.getByRole("table").style.minWidth;
  expect(pageFits(across)).toBe(true);
  expect(across).toBe(
    "calc(3 * var(--size-fact-column) + 1 * var(--size-prose-column) + 1 * var(--size-act))",
  );
});

test("a record's name is the record's, not a link away from it, and no uuid is a column", async () => {
  wire({ "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW]) });
  mount();

  const named = await screen.findByText("daily-brief");
  expect(named.closest("a")).toBeNull();
  expect(TASK_KIND.fields).toContain("conversation");
  expect(headings()).not.toContain("Conversation");
  expect(cells("daily-brief")).not.toContain(CONVO_ID);
});

test("the record's own page stays one press away once its name leads elsewhere", async () => {
  wire({
    "/objects/scheduled_task/daily-brief": () => json(TASK_DETAIL),
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW]),
  });
  mount();

  await openRow("daily-brief");
  expect(await screen.findByRole("heading", { name: "daily-brief" })).toBeTruthy();
  expect(await screen.findByText("write the daily brief")).toBeTruthy();
});

/** The row keeps `last_run_status`, so the ending is proven to read only with the run it belongs
 *  to: alone in the cell, `done` names a run the task has never had. */
test("a field the record lacks takes a dash rather than an empty cell", async () => {
  wire({
    "/objects/scheduled_task": () =>
      objectIndex(TASK_KIND, [
        { ...TASK_ROW, paused: true, next_run_at: null, last_run_at: null },
      ]),
  });
  mount();

  await screen.findByText("daily-brief");
  expect(cells("daily-brief")[2]).toBe("—");
  expect(cells("daily-brief")[3]).toBe("—");
});

test("a value the kind's spec declares as an enum reads as its own chip", async () => {
  wire({
    "/objects/site/docs-abc": () =>
      json({
        ...SITE_KIND,
        name: "docs-abc",
        summary: "docs · workspace · sandbox port 3000",
        spec: { visibility: "workspace" },
        status: { conversation: CONVO_ID, created_at: "2026-07-01T09:00:00Z", visibility: "public" },
        links: [],
        created_at: "2026-07-01T09:00:00Z",
        updated_at: null,
      }),
    "/objects/site": () =>
      objectIndex(SITE_KIND, [{ name: "docs-abc", summary: "docs", visibility: "workspace" }]),
    "/workspace/artifacts": () => json({ artifacts: [] }),
    "/workspace/team": () => json({ members: [], can_add: false, domain: null }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/artifacts";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await userEvent.click(await viewCard("docs-abc"));

  const applied = await screen.findByText("workspace");
  const live = screen.getByText("public");
  for (const chip of [applied, live]) {
    expect(chip.tagName).toBe("SPAN");
    expect(chip.className).toContain("rounded-control");
  }
});

test("an empty index states that the kind has no objects here", async () => {
  wire({ "/objects/scheduled_task": () => objectIndex(TASK_KIND, []) });
  mount();

  expect(await screen.findByText(NO_TASKS)).toBeTruthy();
  expect(screen.queryByRole("table")).toBeNull();
});

test("a deploy without the sites extension leaves the shelf to its files alone", async () => {
  wire({
    "/objects/site": () => new Response("no object kind named 'site'", { status: 404 }),
    "/workspace/artifacts": () => json({ artifacts: [] }),
    "/workspace/team": () => json({ members: [], can_add: false, domain: null }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/artifacts";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByText(NO_ARTIFACTS)).toBeTruthy();
  expect(screen.queryByText(/^Error /)).toBeNull();
});

test("a kind the deploy always installs states the error it got, never an absence", async () => {
  wire({ "/objects/scheduled_task": () => new Response("no such agent", { status: 404 }) });
  mount();

  expect(await screen.findByText("Error 404 — reload to retry.")).toBeTruthy();
  expect(screen.queryByText(NO_TASKS)).toBeNull();
});

test("a read that breaks still states the error, never an empty kind", async () => {
  wire({ "/objects/scheduled_task": () => new Response("nope", { status: 503 }) });
  mount();

  expect(await screen.findByText("Error 503 — reload to retry.")).toBeTruthy();
  expect(screen.queryByText(NO_TASKS)).toBeNull();
});

test("ordering and a boolean filter ride the read, so the kind applies them", async () => {
  const reads: string[] = [];
  wire({
    "/objects/scheduled_task": (url) => {
      reads.push(url);
      return objectIndex(TASK_KIND, [TASK_ROW]);
    },
  });
  mount();

  await screen.findByText("daily-brief");
  expect(reads[0]).toContain("order_by=name");

  await userEvent.click(screen.getByRole("button", { name: "Next Run At" }));
  await waitFor(() => expect(reads.at(-1)).toContain("order_by=next_run_at"));
  expect(reads.at(-1)).not.toContain("order=desc");

  await userEvent.click(screen.getByRole("button", { name: "Next Run At" }));
  await waitFor(() => expect(reads.at(-1)).toContain("order=desc"));

  await userEvent.click(screen.getByRole("tab", { name: "Paused" }));
  await waitFor(() => expect(reads.at(-1)).toContain("paused=true"));
});

test("Mine narrows the scheduled-task read and names its empty scope", async () => {
  const reads: string[] = [];
  wire({
    "/objects/scheduled_task": (url) => {
      reads.push(url);
      return objectIndex(TASK_KIND, url.includes("mine=true") ? [] : [TASK_ROW]);
    },
  });
  mount();

  await screen.findByText("daily-brief");
  await userEvent.click(screen.getByRole("tab", { name: "Mine" }));

  expect(reads.at(-1)).toContain("mine=true");
  expect(await screen.findByText("You have not created a scheduled task.")).toBeTruthy();
});

test("a filter that narrows to nothing keeps the control that clears it", async () => {
  wire({
    "/objects/scheduled_task": (url) =>
      objectIndex(TASK_KIND, url.includes("paused=true") ? [] : [TASK_ROW]),
  });
  mount();

  await screen.findByText("daily-brief");
  await userEvent.click(screen.getByRole("tab", { name: "Paused" }));

  expect(await screen.findByText("No scheduled task matches this search.")).toBeTruthy();
  expect(screen.queryByText(NO_TASKS)).toBeNull();
  expect(headings()).toEqual(["Name", "Agent", "Last Run At", "Next Run At", ""]);
  expect(screen.getByRole("tab", { name: "Paused" }).getAttribute("aria-selected")).toBe("true");

  await userEvent.click(screen.getByRole("tab", { name: "All" }));
  expect(await screen.findByText("daily-brief")).toBeTruthy();
});

test("what a member types rides the read as the kind's own search", async () => {
  const reads: string[] = [];
  wire({
    "/objects/scheduled_task": (url) => {
      reads.push(url);
      return objectIndex(TASK_KIND, url.includes("q=brief") ? [TASK_ROW] : []);
    },
  });
  mount();

  await screen.findByText(NO_TASKS);
  await userEvent.type(screen.getByLabelText("Search scheduled task"), "brief{enter}");

  expect(await screen.findByText("daily-brief")).toBeTruthy();
  expect(reads.at(-1)).toContain("q=brief");

  await userEvent.clear(screen.getByLabelText("Search scheduled task"));
  await userEvent.type(screen.getByLabelText("Search scheduled task"), "nothing{enter}");

  expect(await screen.findByText("No scheduled task matches this search.")).toBeTruthy();
  expect(screen.queryByText(NO_TASKS)).toBeNull();
});

test("the order sits on the head of the column it orders, and only there", async () => {
  wire({ "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW]) });
  mount();

  await screen.findByText("daily-brief");
  const heads = screen.getAllByRole("columnheader");
  expect(heads.map((head) => head.querySelector("button")?.textContent ?? null)).toEqual([
    "Name",
    null,
    "Last Run At",
    "Next Run At",
    null,
  ]);
  expect(heads[0].getAttribute("aria-sort")).toBe("ascending");
  expect(heads[3].getAttribute("aria-sort")).toBe("none");

  await userEvent.click(screen.getByRole("button", { name: "Name" }));
  expect(screen.getAllByRole("columnheader")[0].getAttribute("aria-sort")).toBe("descending");
});

test("the index names no agent, so the read fans out over the whole audience", async () => {
  const reads: string[] = [];
  wire({
    "/objects/scheduled_task": (url) => {
      reads.push(url);
      return objectIndex(TASK_KIND, [TASK_ROW, SECOND_TASK_ROW]);
    },
  });
  mount([AGENT, SECOND]);

  await screen.findByText("daily-brief");
  expect(reads[0]).not.toContain("agent=");
  expect(reads[0]).not.toContain("cursor=");
  expect(cells("daily-brief")[1]).toBe("assistant");
  expect(cells("weekly-roll")[1]).toBe("second");
});

test("one agent's index names that agent, so it neither reads nor draws the owner", async () => {
  const reads: string[] = [];
  wire({
    "/objects/scheduled_task": (url) => {
      reads.push(url);
      return objectIndex(TASK_KIND, [TASK_ROW]);
    },
  });
  mountAgent();

  await screen.findByText("daily-brief");
  expect(reads[0]).toContain("agent=" + AGENT_ID);
  expect(headings()).toEqual(["Name", "Created By", "Last Run At", "Next Run At", ""]);
  expect(screen.queryByRole("link", { name: "assistant" })).toBeNull();
});

test("a page with more behind it walks on the cursor one agent's read returned", async () => {
  const reads: string[] = [];
  wire({
    "/objects/scheduled_task": (url) => {
      reads.push(url);
      return url.includes("cursor=c1")
        ? objectIndex(TASK_KIND, [SECOND_TASK_ROW])
        : objectIndex(TASK_KIND, [TASK_ROW], "c1");
    },
  });
  mountAgent();

  await screen.findByText("daily-brief");
  await userEvent.click(screen.getByRole("button", { name: "Next page" }));

  expect(await screen.findByText("weekly-roll")).toBeTruthy();
  expect(reads.at(-1)).toContain("cursor=c1");

  await userEvent.click(screen.getByRole("button", { name: "First page" }));

  expect(await screen.findByText("daily-brief")).toBeTruthy();
  expect(reads.at(-1)).not.toContain("cursor=");
});

test("a row names the agent that owns it, and leads nowhere but the record", async () => {
  wire({ "/objects/scheduled_task": () => objectIndex(TASK_KIND, [SECOND_TASK_ROW]) });
  mount([AGENT, SECOND]);

  const owner = await screen.findByText("second");
  expect(owner.closest("a")).toBeNull();
  expect(owner.closest("tr")?.getAttribute("tabindex")).toBe("0");
});

test("a detail renders spec, then status, then links, then when the row was made", async () => {
  wire({
    "/objects/scheduled_task/daily-brief": () => json(TASK_DETAIL),
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW]),
  });
  mount();

  await openRow("daily-brief");

  const sections = [...document.querySelectorAll("h2")].map((heading) => heading.textContent);
  expect(sections).toEqual(["daily-brief", "Spec", "Status", "Links"]);
  expect(document.querySelectorAll("h1").length).toBe(0);
  expect(await screen.findByText("write the daily brief")).toBeTruthy();
  expect(fact("Created By")).toBe("mel@example.com");
  expect(screen.queryByText("Owner Email")).toBeNull();
  expect(screen.getByText(/^Created Jul/)).toBeTruthy();
});

test("a spec value longer than its row stands under its label, wrapped, and clears its neighbours", async () => {
  wire({
    "/objects/scheduled_task/daily-brief": () =>
      json({ ...TASK_DETAIL, spec: { ...TASK_DETAIL.spec, prompt: LONG_PROMPT } }),
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW]),
  });
  mount();

  await openRow("daily-brief");

  const prompt = await waitFor(() => specFact("Prompt"));
  expect(prompt.said.textContent).toBe(LONG_PROMPT);
  expect(prompt.said.className).toContain("whitespace-pre-wrap");
  expect(prompt.said.className).toContain("wrap-anywhere");
  expect(prompt.said.className).not.toContain("truncate");
  expect(prompt.row.className).toContain("flex-col");

  const schedule = specFact("Schedule");
  expect(schedule.said.textContent).toBe("0 9 * * *");
  expect(schedule.said.className).toContain("truncate");
  expect(schedule.row.className).toContain("overflow-hidden");
  expect(schedule.row.className).not.toContain("flex-col");
});

test("a spec value with no space to break on wraps in its own block rather than being cut", async () => {
  wire({
    "/objects/scheduled_task/daily-brief": () =>
      json({ ...TASK_DETAIL, spec: { ...TASK_DETAIL.spec, prompt: LONG_URL } }),
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW]),
  });
  mount();

  await openRow("daily-brief");

  const prompt = await waitFor(() => specFact("Prompt"));
  expect(prompt.said.textContent).toBe(LONG_URL);
  expect(prompt.said.className).toContain("wrap-anywhere");
  expect(prompt.row.className).toContain("flex-col");
});

/** One agent's index states who made each row, because the pane names the agent and nothing else on
 *  the row answers whose it is. The index read across the audience states the agent instead: two
 *  columns for one question is what pushed the row's act off the table. */
test("a creator reads as You to its own member, the address to another, Workspace to none", async () => {
  wire({
    "/objects/scheduled_task": () =>
      objectIndex(TASK_KIND, [
        { ...TASK_ROW, owner_email: "mel@example.com" },
        owned({ ...TASK_ROW, name: "mine", owner_email: MEMBER.email }),
        owned({ ...TASK_ROW, name: "standing", owner_email: null }),
      ]),
  });
  render(
    <Viewer.Provider value={MEMBER.email}>
      <MainAgentProvider agents={[AGENT]}>
        <ObjectPane agentId={AGENT_ID} kind="scheduled_task" />
      </MainAgentProvider>
    </Viewer.Provider>,
  );

  await screen.findByText("daily-brief");
  expect(headings()).toEqual(["Name", "Created By", "Last Run At", "Next Run At", ""]);
  expect(cells("daily-brief")[1]).toBe("mel@example.com");
  expect(cells("mine")[1]).toBe("You");
  expect(cells("standing")[1]).toBe("Workspace");
});

test("the index read across the audience names the agent and leaves the creator to the record", async () => {
  wire({ "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW]) });
  mount();

  await screen.findByText("daily-brief");
  expect(headings()).toContain("Agent");
  expect(headings()).not.toContain("Created By");
  expect(screen.queryByText("mel@example.com")).toBeNull();
});

test("a task detail's reports_to link lands on that conversation's detail page", async () => {
  const reads: string[] = [];
  wire({
    ["/objects/conversation/" + CONVO_ID]: () => json(CONVERSATION_DETAIL),
    "/objects/scheduled_task/daily-brief": () => json(TASK_DETAIL),
    "/objects/scheduled_task": (url) => {
      reads.push(url);
      return objectIndex(TASK_KIND, [TASK_ROW]);
    },
  });
  mount();

  await openRow("daily-brief");
  const link = await screen.findByText("reports_to conversation " + CONVO_ID);
  await userEvent.click(link);

  expect(await screen.findByRole("heading", { name: CONVO_ID })).toBeTruthy();
  expect(screen.getAllByText("web").length).toBe(2);
  expect(screen.getByText("member:m1")).toBeTruthy();
  const closed = screen.getByText("scoped_to agent assistant");
  expect(closed.tagName).toBe("SPAN");
  expect(screen.queryByRole("button", { name: "scoped_to agent assistant" })).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  expect(await screen.findByText("daily-brief")).toBeTruthy();
});

test("an outcome stays on the row it happened to, not the next one opened", async () => {
  wire({
    ["/objects/conversation/" + CONVO_ID]: () => json(CONVERSATION_DETAIL),
    "/objects/scheduled_task/daily-brief": () => json(TASK_DETAIL),
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW]),
    "/intents": () => json({ applied: false, message: "The workspace refuses it." }),
  });
  mount();

  await openRow("daily-brief");
  await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
  await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
  expect(await screen.findByText("The workspace refuses it.")).toBeTruthy();

  await userEvent.click(screen.getByText("reports_to conversation " + CONVO_ID));

  expect(await screen.findByRole("heading", { name: CONVO_ID })).toBeTruthy();
  expect(screen.queryByText("The workspace refuses it.")).toBeNull();
});

test("a spec the kind elides reads as the row's own summary, with no form to submit", async () => {
  wire({
    "/objects/scheduled_task/daily-brief": () =>
      json({ ...TASK_DETAIL, spec: null, summary: "0 9 * * * — private member task" }),
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW]),
  });
  mount();

  await openRow("daily-brief");

  expect(await screen.findByText("0 9 * * * — private member task")).toBeTruthy();
  expect(screen.queryByLabelText("prompt")).toBeNull();
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  expect(screen.getByRole("button", { name: "Delete" })).toBeTruthy();
});

test("a deleted row lands back on the index; a refused delete states the refusal in place", async () => {
  const posted: unknown[] = [];
  let deleted = false;
  wire({
    "/objects/scheduled_task/daily-brief": () => json(TASK_DETAIL),
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, deleted ? [] : [TASK_ROW]),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      if (!deleted) return json({ applied: false, message: "The workspace refuses it." });
      return json({ applied: true, message: "Deleted daily-brief." });
    },
  });
  mount();

  await openRow("daily-brief");
  await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
  await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

  expect(await screen.findByText("The workspace refuses it.")).toBeTruthy();
  expect(screen.getByRole("heading", { name: "daily-brief" })).toBeTruthy();

  deleted = true;
  await userEvent.click(screen.getByRole("button", { name: "Delete" }));
  await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

  expect(await screen.findByText(NO_TASKS)).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "daily-brief" })).toBeNull();
  expect(posted).toEqual([
    { verb: "delete", kind: "scheduled_task", name: "daily-brief" },
    { verb: "delete", kind: "scheduled_task", name: "daily-brief" },
  ]);
});

test("the radar section is reached by its own hash and leads with the feed", async () => {
  const reads: string[] = [];
  const listed: string[] = [];
  wire({
    "/workspace/radar": (url) => {
      reads.push(url);
      return json({ runs: [RUN], older: null, newer: null });
    },
    "/objects/scheduled_task": (url) => {
      listed.push(url);
      return objectIndex(TASK_KIND, [TASK_ROW, SECOND_TASK_ROW]);
    },
    "/objects/source_trigger": () => objectIndex(TRIGGER_KIND, []),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/radar";
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByRole("heading", { level: 1, name: "Radar" })).toBeTruthy();
  expect(await screen.findByText("roll-up.pdf")).toBeTruthy();
  expect(reads[0]).not.toContain("agent=");
  expect(screen.getByRole("tab", { name: "Runs" }).getAttribute("aria-selected")).toBe("true");

  await userEvent.click(screen.getByRole("tab", { name: "Scheduled" }));
  expect(await screen.findByText("weekly-roll")).toBeTruthy();
  expect(listed[0]).not.toContain("agent=");
});

test("the radar tab of an agent narrows the feed and both kinds to its namespace", async () => {
  const reads: string[] = [];
  wire({
    "/workspace/radar": (url) => {
      reads.push(url);
      return json({ runs: [RUN], older: null, newer: null });
    },
    "/objects/scheduled_task": (url) => {
      reads.push(url);
      return objectIndex(TASK_KIND, [TASK_ROW]);
    },
    "/objects/source_trigger": (url) => {
      reads.push(url);
      return objectIndex(TRIGGER_KIND, []);
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "/radar";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByText("roll-up.pdf")).toBeTruthy();
  expect(reads[0]).toContain("agent=" + AGENT_ID);
  expect(screen.queryByRole("heading", { level: 1, name: "Radar" })).toBeNull();

  await userEvent.click(screen.getByRole("tab", { name: "Scheduled" }));
  expect(await screen.findByText("daily-brief")).toBeTruthy();
  await userEvent.click(screen.getByRole("tab", { name: "Triggers" }));
  await waitFor(() =>
    expect(reads.some((read) => read.includes("/objects/source_trigger"))).toBe(true),
  );
  expect(reads.every((read) => read.includes("agent=" + AGENT_ID))).toBe(true);
  expect(screen.getByRole("button", { name: "Radar" }).getAttribute("aria-current")).toBe(
    "false",
  );
});

test("landing on another agent's radar tab leaves the first agent's detail behind", async () => {
  wire({
    "/objects/scheduled_task/daily-brief": () => json(TASK_DETAIL),
    "/objects/scheduled_task": (url) =>
      objectIndex(TASK_KIND, url.includes(AGENT_ID) ? [TASK_ROW] : [SECOND_TASK_ROW]),
    "/objects/source_trigger": () => objectIndex(TRIGGER_KIND, []),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "/radar?chip=scheduled_task";
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await openRow("daily-brief");
  await screen.findByRole("heading", { name: "daily-brief" });

  location.hash = "#/agents/" + SECOND_ID + "/radar?chip=scheduled_task";
  window.dispatchEvent(new HashChangeEvent("hashchange"));

  expect(await screen.findByText("weekly-roll")).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "daily-brief" })).toBeNull();
});

test("a row opens under the agent that owns it, and Back returns to the whole index", async () => {
  const reads: string[] = [];
  wire({
    "/objects/scheduled_task/weekly-roll": (url) => {
      reads.push(url);
      return json({ ...TASK_DETAIL, name: "weekly-roll", summary: "0 9 * * 1 — weekly roll-up" });
    },
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW, SECOND_TASK_ROW]),
    "/objects/source_trigger": () => objectIndex(TRIGGER_KIND, []),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/radar?chip=scheduled_task";
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await openRow("weekly-roll");

  expect(await screen.findByRole("heading", { name: "weekly-roll" })).toBeTruthy();
  expect(reads[0]).toContain("agent=" + SECOND_ID);

  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  expect(await screen.findByText("daily-brief")).toBeTruthy();
});

test("the artifacts section reads the site index on the main agent", async () => {
  const reads: string[] = [];
  wire({
    "/objects/site": (url) => {
      reads.push(url);
      return objectIndex(SITE_KIND, []);
    },
    "/workspace/artifacts": () => json({ artifacts: [] }),
    "/workspace/team": () => json({ members: [], can_add: false, domain: null }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/artifacts";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByText(NO_ARTIFACTS)).toBeTruthy();
  expect(reads[0]).toContain("agent=" + AGENT_ID);
  expect(screen.queryByText(NO_TASKS)).toBeNull();
});

test("the act that writes an object opens over the index, and a refusal keeps it open", async () => {
  wire({
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW]),
    "/intents": () => json({ applied: false, message: "The workspace refuses it." }),
  });
  mount();

  expect(await screen.findByText("daily-brief")).toBeTruthy();
  expect(screen.queryByLabelText("Name")).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "New scheduled task" }));
  await userEvent.type(await screen.findByLabelText("Name"), "digest");
  await userEvent.click(screen.getByRole("button", { name: "Create" }));

  expect(await screen.findByText("The workspace refuses it.")).toBeTruthy();
  expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("digest");
});

test("an object the lane accepts closes the act it was written through", async () => {
  wire({
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW]),
    "/intents": () => json({ applied: true }),
  });
  mount();

  await userEvent.click(await screen.findByRole("button", { name: "New scheduled task" }));
  await userEvent.type(await screen.findByLabelText("Name"), "digest");
  await userEvent.click(screen.getByRole("button", { name: "Create" }));

  await waitFor(() => expect(screen.queryByLabelText("Name")).toBeNull());
});

const EXPIRING_KIND = {
  ...TASK_KIND,
  spec_schema: {
    properties: {
      ...TASK_KIND.spec_schema.properties,
      expires_at: { anyOf: [{ type: "string", format: "date-time" }, { type: "null" }] },
    },
  },
};

const LOCAL_EXPIRY = "2026-09-01T09:00";

test("a moment typed on the clock in front of the member is submitted as an instant", async () => {
  const posted: { spec: Record<string, string> }[] = [];
  wire({
    "/objects/scheduled_task": () => objectIndex(EXPIRING_KIND, [TASK_ROW]),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true });
    },
  });
  mount();

  await userEvent.click(await screen.findByRole("button", { name: "New scheduled task" }));
  await userEvent.type(await screen.findByLabelText("Name"), "digest");
  fireEvent.change(screen.getByLabelText("expires_at"), { target: { value: LOCAL_EXPIRY } });
  await userEvent.click(screen.getByRole("button", { name: "Create" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0].spec.expires_at).toBe(new Date(LOCAL_EXPIRY).toISOString());
  expect(posted[0].spec.expires_at.endsWith("Z")).toBe(true);
});

test("an instant off the wire is shown on the clock in front of the member", async () => {
  const instant = new Date(LOCAL_EXPIRY).toISOString();
  wire({
    "/objects/scheduled_task/daily-brief": () =>
      json({ ...TASK_DETAIL, ...EXPIRING_KIND, spec: { ...TASK_DETAIL.spec, expires_at: instant } }),
    "/objects/scheduled_task": () => objectIndex(EXPIRING_KIND, [TASK_ROW]),
  });
  mount();

  await openRow("daily-brief");
  await userEvent.click(await screen.findByRole("button", { name: "Edit" }));

  expect((await screen.findByLabelText("expires_at")).getAttribute("value")).toBe(LOCAL_EXPIRY);
});

test("a record is deleted from the record's own page, and a refusal says so there", async () => {
  const posted: unknown[] = [];
  wire({
    "/objects/scheduled_task/daily-brief": () => json(TASK_DETAIL),
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW]),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: false, message: "The workspace refuses it." });
    },
  });
  mount();

  await openRow("daily-brief");
  await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
  expect(posted.length).toBe(0);
  await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({ verb: "delete", kind: "scheduled_task", name: "daily-brief" });
  await refusedNotice("The workspace refuses it.");
});

test("a record is deleted through the lane of the agent that owns it", async () => {
  const lanes: string[] = [];
  wire({
    "/objects/scheduled_task/weekly-roll": () =>
      json({ ...TASK_DETAIL, name: "weekly-roll", summary: "0 9 * * 1 — weekly roll-up" }),
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW, SECOND_TASK_ROW]),
    "/intents": (url) => {
      lanes.push(url);
      return json({ applied: true, message: "Deleted weekly-roll." });
    },
  });
  mount([AGENT, SECOND]);

  await openRow("weekly-roll");
  await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
  await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

  await waitFor(() => expect(lanes.length).toBe(1));
  expect(lanes[0]).toContain("/agents/" + SECOND_ID + "/intents");
});

test("the act that writes a task asks which agent runs it, and writes to that lane", async () => {
  const lanes: string[] = [];
  wire({
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW]),
    "/intents": (url) => {
      lanes.push(url);
      return json({ applied: true });
    },
  });
  mount([AGENT, SECOND]);

  await userEvent.click(await screen.findByRole("button", { name: "New scheduled task" }));
  await pick("Agent", "second");
  await userEvent.type(screen.getByLabelText("Name"), "digest");
  await userEvent.click(screen.getByRole("button", { name: "Create" }));

  await waitFor(() => expect(lanes.length).toBe(1));
  expect(lanes[0]).toContain("/agents/" + SECOND_ID + "/intents");
});

test("one agent in the audience is no choice, so the act asks for none", async () => {
  const lanes: string[] = [];
  wire({
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW]),
    "/intents": (url) => {
      lanes.push(url);
      return json({ applied: true });
    },
  });
  mount();

  await userEvent.click(await screen.findByRole("button", { name: "New scheduled task" }));
  expect(screen.queryByRole("combobox", { name: "Agent" })).toBeNull();
  await userEvent.type(screen.getByLabelText("Name"), "digest");
  await userEvent.click(screen.getByRole("button", { name: "Create" }));

  await waitFor(() => expect(lanes.length).toBe(1));
  expect(lanes[0]).toContain("/agents/" + AGENT_ID + "/intents");
});

test("a kind the lane declines to write offers no act on its rows", async () => {
  wire({
    "/objects/scheduled_task": () =>
      objectIndex({ ...TASK_KIND, applies: false, deletes: false }, [TASK_ROW]),
  });
  mount();

  await screen.findByText("daily-brief");
  expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  expect(screen.queryByRole("button", { name: "New scheduled task" })).toBeNull();
});

const TRIGGER_NAME = "github-1a2b3c4d-" + CONVO_ID.replaceAll("-", "");

const TRIGGER_ROW = owned({
  name: TRIGGER_NAME,
  summary: "github (acct-one): pull_requests, issues",
  conversation: CONVO_ID,
  source: "github-1a2b3c4d",
  delivery: "per_page",
  origin: "#general",
  owner_email: "mel@example.com",
  mine: true,
});

test("Radar shows one family at a time and the switcher names which", async () => {
  const reads: string[] = [];
  wire({
    "/workspace/radar": (url) => {
      reads.push(url);
      return json({ runs: [RUN], older: null, newer: null });
    },
    "/objects/scheduled_task": (url) => {
      reads.push(url);
      return objectIndex(TASK_KIND, [TASK_ROW]);
    },
    "/objects/source_trigger": (url) => {
      reads.push(url);
      return objectIndex(TRIGGER_KIND, [TRIGGER_ROW]);
    },
  });
  mountRadar();

  expect(await screen.findByRole("heading", { level: 1, name: "Radar" })).toBeTruthy();
  expect(await screen.findByText("roll-up.pdf")).toBeTruthy();
  expect(reads.every((read) => read.includes("/workspace/radar"))).toBe(true);

  await userEvent.click(screen.getByRole("tab", { name: "Scheduled" }));

  expect(await screen.findByText("daily-brief")).toBeTruthy();
  expect(screen.queryByText("roll-up.pdf")).toBeNull();

  await userEvent.click(screen.getByRole("tab", { name: "Triggers" }));

  expect(await screen.findByText(TRIGGER_NAME)).toBeTruthy();
  expect(screen.queryByText("daily-brief")).toBeNull();
  expect(reads.some((read) => read.includes("/objects/source_trigger"))).toBe(true);
});

test("a trigger is ended from its own page and never created from one", async () => {
  const posted: unknown[] = [];
  wire({
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, []),
    ["/objects/source_trigger/" + TRIGGER_NAME]: () =>
      json({
        ...TRIGGER_KIND,
        name: TRIGGER_NAME,
        summary: "github (acct-one): pull_requests",
        spec: { source: "github-1a2b3c4d", delivery: "per_page" },
        status: { source: "github-1a2b3c4d", delivery: "per_page", origin: "#general" },
        links: [],
        created_at: "2026-08-01T09:00:00Z",
        updated_at: null,
      }),
    "/objects/source_trigger": () => objectIndex(TRIGGER_KIND, [TRIGGER_ROW]),
    "/workspace/radar": () => json({ runs: [] }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Deleted the trigger." });
    },
  });
  mountRadar();

  await userEvent.click(await screen.findByRole("tab", { name: "Triggers" }));
  expect(screen.queryByRole("button", { name: "New source trigger" })).toBeNull();
  await openRow(TRIGGER_NAME);

  expect(await screen.findByRole("heading", { name: TRIGGER_NAME })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Delete" }));
  await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({
    verb: "delete",
    kind: "source_trigger",
    name: TRIGGER_NAME,
  });
});

test("a section with no trigger says so without saying none was ever made", async () => {
  wire({
    "/workspace/radar": () => json({ runs: [] }),
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, []),
    "/objects/source_trigger": () => objectIndex(TRIGGER_KIND, []),
  });
  mountRadar();

  expect(await screen.findByText(NO_RUNS)).toBeTruthy();
  await userEvent.click(screen.getByRole("tab", { name: "Scheduled" }));
  expect(await screen.findByText(NO_TASKS)).toBeTruthy();
  await userEvent.click(screen.getByRole("tab", { name: "Triggers" }));
  expect(await screen.findByText(NO_TRIGGERS)).toBeTruthy();
});
