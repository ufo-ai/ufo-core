import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { ObjectDetail, ObjectPane } from "@/kernel/objects";
import type { Placement } from "@/kernel/pager";
import { Pane } from "@/kernel/pane";
import { Viewer } from "@/lib/audience";
import { friendlyMoment, fullMoment } from "@/lib/moments";
import { MainAgentProvider } from "@/lib/mainAgent";
import { chatHash } from "@/lib/route";

import {
  AGENT,
  AGENT_ID,
  CONVO_ID,
  declaredFloor,
  fact,
  json,
  MEMBER,
  NO_TASKS,
  objectIndex,
  openAgentSettings,
  openRow,
  owned,
  pageFits,
  pick,
  refusedNotice,
  SECOND,
  SECOND_ID,
  SETTINGS,
  TASK_KIND,
  TRIGGER_KIND,
  useStreamFake,
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

/** A trigger's own name carries the conversation's hex, so what the screen draws is its summary. */
const ISSUES = "github-30847ee4 — issues";
const PULLS = "github-30847ee4 — pull_requests";

/** A memory item is named by its uuid, so what the screen draws is its summary line. */
const MEMORY_LINE = "what the member decided";

const TRIGGER_ROW = owned({
  name: "github-issues",
  summary: "github-30847ee4 — issues",
  conversation: CONVO_ID,
  source: "github-30847ee4",
  delivery: "current",
  mine: true,
  origin: "#general",
  owner_email: "mel@example.com",
});

const SECOND_TRIGGER_ROW = owned(
  {
    name: "github-pulls",
    summary: "github-30847ee4 — pull_requests",
    conversation: SECOND_CONVO_ID,
    source: "github-30847ee4",
    delivery: "per_page",
  },
  SECOND,
);

const TRIGGER_DETAIL = {
  ...TRIGGER_KIND,
  name: "github-issues",
  summary: "github-30847ee4 — issues",
  spec: { source: "github-30847ee4", delivery: "current" },
  status: { source: "github-30847ee4", owner_email: "mel@example.com" },
  links: [{ relation: "reports_to", kind: "conversation", name: CONVO_ID, opens: true }],
  created_at: "2026-07-01T09:00:00Z",
  updated_at: "2026-07-02T09:00:00Z",
};

const MEMORY_KIND = {
  kind: "memory",
  fields: ["item_class", "memory_kind", "subject"],
  spec_schema: {
    properties: {
      body: { type: "string", title: "Body" },
      memory_kind: { type: "string", title: "Memory Kind" },
    },
  },
  applies: false,
  deletes: false,
};

const MEMORY_ROW = owned({
  name: "mem-1",
  summary: "what the member decided",
  item_class: "fact",
  memory_kind: "decision",
  subject: "workspace",
});

const memoryDetail = (body: string) => ({
  ...MEMORY_KIND,
  name: "mem-1",
  summary: "what the member decided",
  spec: { body, memory_kind: "decision" },
  status: { subject: "workspace" },
  links: [],
  created_at: "2026-07-01T09:00:00Z",
  updated_at: null,
});

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

function standing(): (string | null)[] {
  return screen
    .queryAllByRole("dialog")
    .filter((dialog) => dialog.getAttribute("data-slot") === "sheet-content")
    .map((dialog) => {
      const title = dialog.getAttribute("aria-labelledby");
      return title ? document.getElementById(title)?.textContent ?? null : null;
    });
}

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

function PlacedPane({ agentId, kind }: { agentId: string | null; kind: string }) {
  const [place, setPlace] = useState<Placement>({});
  return (
    <ObjectPane
      agentId={agentId}
      kind={kind}
      opens={place.opens ?? []}
      onPlace={(patch) => setPlace((held) => ({ ...held, ...patch }))}
    />
  );
}

function mount(agents = [AGENT], kind = "scheduled_task") {
  render(
    <MainAgentProvider agents={agents}>
      <Pane>
        <PlacedPane agentId={null} kind={kind} />
      </Pane>
    </MainAgentProvider>,
  );
}

function mountAgent() {
  render(
    <MainAgentProvider agents={[AGENT, SECOND]}>
      <Pane>
        <PlacedPane agentId={AGENT_ID} kind="scheduled_task" />
      </Pane>
    </MainAgentProvider>,
  );
}

const LINKED_TRIGGER = {
  ...TRIGGER_DETAIL,
  links: [
    { relation: "reports_to", kind: "conversation", name: CONVO_ID, opens: true },
    { relation: "follows", kind: "source_trigger", name: "github-pulls", opens: true },
  ],
};

const PULLS_DETAIL = { ...TRIGGER_DETAIL, name: "github-pulls", summary: PULLS, links: [] };

function triggers() {
  return wire({
    ["/objects/conversation/" + CONVO_ID]: () => json(CONVERSATION_DETAIL),
    "/objects/source_trigger/github-issues": () => json(LINKED_TRIGGER),
    "/objects/source_trigger/github-pulls": () => json(PULLS_DETAIL),
    "/objects/source_trigger": () =>
      objectIndex(TRIGGER_KIND, [TRIGGER_ROW, SECOND_TRIGGER_ROW]),
  });
}

function link(said: string): HTMLElement {
  return screen.getByRole("button", { name: said });
}

const FOLLOWS = "follows source trigger github-pulls";

/** One `userEvent` instance holds the key down across the click; the module's own verbs each set up a
 *  fresh one and would let go of it in between. */
async function besidePress(target: HTMLElement): Promise<void> {
  const user = userEvent.setup();
  await user.keyboard("{Meta>}");
  await user.click(target);
  await user.keyboard("{/Meta}");
}


test("a moment reads as its distance from now, and as its date once it is old", () => {
  expect(friendlyMoment("2026-08-01T11:59:30Z", NOW)).toBe("now");
  expect(friendlyMoment("2026-08-01T11:30:00Z", NOW)).toBe("30m ago");
  expect(friendlyMoment("2026-08-01T09:00:00Z", NOW)).toBe("3h ago");
  expect(friendlyMoment("2026-07-31T11:00:00Z", NOW)).toBe("Yesterday");
  expect(friendlyMoment("2026-07-29T12:00:00Z", NOW)).toBe("3d ago");
  expect(friendlyMoment("2026-07-25T12:00:00Z", NOW)).toBe("Jul 25 2026");
  expect(friendlyMoment("2026-08-01T15:00:00Z", NOW)).toBe("in 3h");
  expect(friendlyMoment("2026-08-02T13:00:00Z", NOW)).toBe("Tomorrow");
  expect(friendlyMoment("2026-09-01T12:00:00Z", NOW)).toBe("Sep 1 2026");
  expect(friendlyMoment("not a moment", NOW)).toBe("not a moment");
});

test("the whole stamp behind a friendly one names the day and the time it fell on", () => {
  expect(fullMoment("2026-08-01T09:05:00Z")).toBe("Aug 1 2026 at 09:05 UTC");
});

test("an index carries the name and the two facts a prose-less kind leads with", async () => {
  wire({ "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW]) });
  mount();

  const name = await screen.findByText("daily-brief");
  const row = name.closest("tr");
  expect(row).not.toBeNull();
  expect(headings()).toEqual(["Name", "App", "Last Run At", "Next Run At", ""]);
  const said = cells("daily-brief");
  expect(said[0]).toBe("daily-briefActive");
  expect(said[1]).toBe("Assistant");
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
  expect(screen.queryByRole("tab", { name: "Paused" })).toBeNull();
});

/** The tracks are fixed pixels, so a table whose tracks outrun its page scrolls the column sideways and
 *  what falls off the right is the act the row is pressed by. The desktop has to clear the sum. */
test("a record's time reads as its distance from now and keeps the whole stamp on hover", async () => {
  wire({ "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW]) });
  mount();

  await screen.findByText("daily-brief");
  const stamp = screen.getByText("in 3h", { selector: "time" });
  expect(stamp.getAttribute("datetime")).toBe(IN_THREE_HOURS);
  expect(stamp.getAttribute("title")).toBe(fullMoment(IN_THREE_HOURS));
});

test("an index fits the desktop it is read on, so the row's act never scrolls off", async () => {
  wire({ "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW]) });
  mount();

  await screen.findByText("daily-brief");
  const across = declaredFloor(screen.getByRole("table"));
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
  const across = declaredFloor(screen.getByRole("table"));
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

test("an empty index states that the kind has no objects here", async () => {
  wire({ "/objects/scheduled_task": () => objectIndex(TASK_KIND, []) });
  mount();

  expect(await screen.findByText(NO_TASKS)).toBeTruthy();
  expect(screen.queryByRole("table")).toBeNull();
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

  await userEvent.click(screen.getByRole("tab", { name: "Created by me" }));
  await waitFor(() => expect(reads.at(-1)).toContain("mine=true"));
});

test("Created by me narrows the read, names its empty scope, and All clears it", async () => {
  const reads: string[] = [];
  wire({
    "/objects/scheduled_task": (url) => {
      reads.push(url);
      return objectIndex(TASK_KIND, url.includes("mine=true") ? [] : [TASK_ROW]);
    },
  });
  mount();

  await screen.findByText("daily-brief");
  await userEvent.click(screen.getByRole("tab", { name: "Created by me" }));

  expect(reads.at(-1)).toContain("mine=true");
  expect(await screen.findByText("You have not created a scheduled task.")).toBeTruthy();
  expect(screen.queryByText(NO_TASKS)).toBeNull();
  expect(headings()).toEqual(["Name", "App", "Last Run At", "Next Run At", ""]);
  expect(
    screen.getByRole("tab", { name: "Created by me" }).getAttribute("aria-selected"),
  ).toBe("true");

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
  expect(cells("daily-brief")[1]).toBe("Assistant");
  expect(cells("weekly-roll")[1]).toBe("Second");
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
  expect(screen.queryByRole("link", { name: "Assistant" })).toBeNull();
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

  const owner = await screen.findByText("Second");
  expect(owner.closest("a")).toBeNull();
  expect(owner.closest("tr")?.getAttribute("tabindex")).toBe("0");
});

test("a detail renders spec, then status, then links, then when the row was made", async () => {
  wire({
    "/objects/source_trigger/github-issues": () => json(TRIGGER_DETAIL),
    "/objects/source_trigger": () => objectIndex(TRIGGER_KIND, [TRIGGER_ROW]),
  });
  mount([AGENT], "source_trigger");

  await openRow(ISSUES);

  const sections = [...document.querySelectorAll("h2")].map((heading) => heading.textContent);
  expect(sections).toEqual([ISSUES, "Spec", "Status", "Links"]);
  expect(document.querySelectorAll("h1").length).toBe(0);
  expect(await waitFor(() => specFact("Delivery"))).toHaveProperty("said.textContent", "current");
  expect(fact("Created By")).toBe("mel@example.com");
  expect(screen.queryByText("Owner Email")).toBeNull();
  expect(screen.getByText("Created").textContent).toBe("Created Jul 1 2026");
});

test("a record header action applies a partial spec and reads back its next state", async () => {
  const posted: unknown[] = [];
  let delivery = "current";
  wire({
    "/objects/source_trigger/github-issues": () =>
      json({
        ...TRIGGER_DETAIL,
        spec: { ...TRIGGER_DETAIL.spec, delivery },
        status: { ...TRIGGER_DETAIL.status, delivery },
      }),
    "/intents": (_url, init) => {
      const envelope = JSON.parse(String(init?.body));
      posted.push(envelope);
      delivery = envelope.spec.delivery;
      return json({ applied: true, message: "Applied." });
    },
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <ObjectDetail
        agentId={AGENT_ID}
        kind="source_trigger"
        name="github-issues"
        actions={(status, apply) =>
          status === null ? null : (
            <button
              type="button"
              onClick={() =>
                void apply({ delivery: status.delivery === "current" ? "per_page" : "current" })
              }
            >
              {status.delivery === "current" ? "Wake per page" : "Wake this chat"}
            </button>
          )
        }
        onOpen={() => {}}
        onBack={() => {}}
      />
    </MainAgentProvider>,
  );

  await userEvent.click(await screen.findByRole("button", { name: "Wake per page" }));

  expect(await screen.findByRole("button", { name: "Wake this chat" })).toBeTruthy();
  expect(await screen.findByText("Applied.")).toBeTruthy();
  expect(posted).toEqual([
    {
      verb: "apply",
      kind: "source_trigger",
      name: "github-issues",
      spec: { delivery: "per_page" },
    },
  ]);
});

test("a spec value longer than its row stands under its label, wrapped, and clears its neighbours", async () => {
  wire({
    "/objects/memory/mem-1": () => json(memoryDetail(LONG_PROMPT)),
    "/objects/memory": () => objectIndex(MEMORY_KIND, [MEMORY_ROW]),
  });
  mount([AGENT], "memory");

  await openRow(MEMORY_LINE);

  const body = await waitFor(() => specFact("Body"));
  expect(body.said.textContent).toBe(LONG_PROMPT);
  expect(body.said.className).toContain("whitespace-pre-wrap");
  expect(body.said.className).toContain("wrap-anywhere");
  expect(body.said.className).not.toContain("truncate");
  expect(body.row.className).toContain("flex-col");

  const kind = specFact("Memory Kind");
  expect(kind.said.textContent).toBe("decision");
  expect(kind.said.className).toContain("truncate");
  expect(kind.row.className).toContain("overflow-hidden");
  expect(kind.row.className).not.toContain("flex-col");
});

test("a spec value with no space to break on wraps in its own block rather than being cut", async () => {
  wire({
    "/objects/memory/mem-1": () => json(memoryDetail(LONG_URL)),
    "/objects/memory": () => objectIndex(MEMORY_KIND, [MEMORY_ROW]),
  });
  mount([AGENT], "memory");

  await openRow(MEMORY_LINE);

  const body = await waitFor(() => specFact("Body"));
  expect(body.said.textContent).toBe(LONG_URL);
  expect(body.said.className).toContain("wrap-anywhere");
  expect(body.row.className).toContain("flex-col");
});

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
        <Pane>
          <PlacedPane agentId={AGENT_ID} kind="scheduled_task" />
        </Pane>
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
  expect(headings()).toContain("App");
  expect(headings()).not.toContain("Created By");
  expect(screen.queryByText("mel@example.com")).toBeNull();
});

test("an object link replaces the visible sheet and closing returns to its source", async () => {
  triggers();
  mount([AGENT], "source_trigger");

  await openRow(ISSUES);
  expect(standing()).toEqual([ISSUES]);

  await userEvent.click(await screen.findByRole("button", { name: FOLLOWS }));
  const pulls = await screen.findByRole("dialog", { name: PULLS });
  expect(standing()).toEqual([PULLS]);

  await userEvent.click(within(pulls).getByRole("button", { name: "Close" }));
  await waitFor(() => expect(standing()).toEqual([ISSUES]));
});

test("an open conversation link goes to the conversation", async () => {
  wire({
    "/objects/notification/n-1": () =>
      json({
        kind: "notification",
        fields: ["occurrences", "triaged"],
        spec_schema: null,
        applies: false,
        deletes: true,
        name: "n-1",
        summary: "A notification.",
        spec: { subject: "invoice", body: "The invoice is ready." },
        status: { occurrences: 1, triaged: false },
        links: [
          { relation: "created_in", kind: "conversation", name: CONVO_ID, opens: true },
        ],
        created_at: "2026-07-01T09:00:00Z",
        updated_at: null,
      }),
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <Pane>
        <ObjectDetail
          agentId={AGENT_ID}
          kind="notification"
          name="n-1"
          onOpen={() => {}}
          onBack={() => {}}
        />
      </Pane>
    </MainAgentProvider>,
  );

  const conversation = await screen.findByRole("link", { name: "Conversation" });
  expect(conversation.getAttribute("href")).toBe(chatHash(CONVO_ID));
  expect(conversation.closest('[data-part="link"]')?.textContent).toBe("created_in Conversation");
});

test("a modifier press retains the source behind the one visible sheet", async () => {
  triggers();
  mount([AGENT], "source_trigger");

  await openRow(ISSUES);
  await besidePress(link(FOLLOWS));

  const pulls = await screen.findByRole("dialog", { name: PULLS });
  expect(standing()).toEqual([PULLS]);
  await userEvent.click(within(pulls).getByRole("button", { name: "Close" }));
  await waitFor(() => expect(standing()).toEqual([ISSUES]));
});

test("a row of the index shuts every record standing and opens the one it names", async () => {
  triggers();
  mount([AGENT], "source_trigger");

  await openRow(ISSUES);
  await userEvent.click(await screen.findByRole("button", { name: FOLLOWS }));
  expect(await screen.findByRole("dialog", { name: PULLS })).toBeTruthy();

  await openRow(PULLS);

  await waitFor(() => expect(standing()).toEqual([PULLS]));
});

test("closing the current record returns to the record opened from it", async () => {
  triggers();
  mount([AGENT], "source_trigger");

  await openRow(ISSUES);
  await userEvent.click(await screen.findByRole("button", { name: FOLLOWS }));
  const pulls = await screen.findByRole("dialog", { name: PULLS });

  await userEvent.click(within(pulls).getByRole("button", { name: "Close" }));

  await waitFor(() => expect(standing()).toEqual([ISSUES]));
});

test("the index row whose record is standing is marked, and no other", async () => {
  wire({
    "/objects/scheduled_task/daily-brief": () => json(TASK_DETAIL),
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW, SECOND_TASK_ROW]),
  });
  mount();

  await openRow("daily-brief");

  expect(await screen.findByRole("dialog", { name: "daily-brief" })).toBeTruthy();
  const marked = screen
    .getAllByRole("row")
    .filter((row) => row.getAttribute("aria-current") === "true");
  expect(marked).toHaveLength(1);
  expect(marked[0].textContent).toContain("daily-brief");
});

test("an outcome does not leak into the sheet that replaces its record", async () => {
  wire({
    "/objects/source_trigger/github-issues": () => json(LINKED_TRIGGER),
    "/objects/source_trigger/github-pulls": () => json(PULLS_DETAIL),
    "/objects/source_trigger": () => objectIndex(TRIGGER_KIND, [TRIGGER_ROW]),
    "/intents": () => json({ applied: false, message: "The workspace refuses it." }),
  });
  mount([AGENT], "source_trigger");

  await openRow(ISSUES);
  await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
  await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
  expect(await screen.findByText("The workspace refuses it.")).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: FOLLOWS }));

  const opened = await screen.findByRole("dialog", { name: PULLS });
  expect(within(opened).queryByText("The workspace refuses it.")).toBeNull();
  expect(standing()).toEqual([PULLS]);
});

test("a spec the kind elides reads as the row's own summary, with no form to submit", async () => {
  wire({
    "/objects/source_trigger/github-issues": () =>
      json({ ...TRIGGER_DETAIL, spec: null, summary: "a source another member watches" }),
    "/objects/source_trigger": () => objectIndex(TRIGGER_KIND, [TRIGGER_ROW]),
  });
  mount([AGENT], "source_trigger");

  await openRow(ISSUES);

  const elided = await screen.findByRole("dialog", { name: "a source another member watches" });
  expect(
    within(elided).getByText("a source another member watches", { selector: "p" }),
  ).toBeTruthy();
  expect(screen.queryByLabelText("source")).toBeNull();
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

test("an app's Scheduled tab lists that app's tasks, and a row opens inside the dialog", async () => {
  const listed: string[] = [];
  wire({
    "/settings": () => json(SETTINGS),
    "/objects/scheduled_task/daily-brief": () => json(TASK_DETAIL),
    "/objects/scheduled_task": (url) => {
      listed.push(url);
      return objectIndex(TASK_KIND, [TASK_ROW]);
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  const dialog = await openAgentSettings("Assistant", "Scheduled");

  expect(await within(dialog).findByText("daily-brief")).toBeTruthy();
  expect(listed.every((read) => read.includes("agent=" + AGENT_ID))).toBe(true);
  expect(within(dialog).queryByRole("columnheader", { name: "App" })).toBeNull();

  await openRow("daily-brief");

  const sheet = await screen.findByRole("dialog", { name: "daily-brief" });
  expect(within(sheet).getByText("write the daily brief")).toBeTruthy();
  expect(dialog).toBeTruthy();
});

test("an app's Scheduled tab offers no act that writes a task", async () => {
  wire({
    "/settings": () => json(SETTINGS),
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW]),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  const dialog = await openAgentSettings("Assistant", "Scheduled");

  expect(await within(dialog).findByText("daily-brief")).toBeTruthy();
  expect(within(dialog).queryByRole("button", { name: "New scheduled task" })).toBeNull();
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
  await pick("App", "Second");
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
  expect(screen.queryByRole("combobox", { name: "App" })).toBeNull();
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

const NOTIFICATION_KIND = {
  kind: "notification",
  fields: ["subject", "occurrences", "triaged"],
  spec_schema: null,
  applies: false,
  deletes: true,
};

const NOTIFICATION_NAME = "5b7fbd1e9f0a4c7f8d2e1a3b4c5d6e7f";
const NOTIFICATION_SUBJECT = "The invoice is ready";

const NOTIFICATION_ROW = owned({
  name: NOTIFICATION_NAME,
  summary: NOTIFICATION_SUBJECT + ": it needs a signature.",
  subject: NOTIFICATION_SUBJECT,
  occurrences: 2,
  triaged: false,
});

const NOTIFICATION_DETAIL = {
  ...NOTIFICATION_KIND,
  name: NOTIFICATION_NAME,
  summary: NOTIFICATION_SUBJECT + ": it needs a signature.",
  spec: null,
  status: { subject: NOTIFICATION_SUBJECT, occurrences: 2, triaged: false },
  links: [],
  created_at: "2026-07-01T09:00:00Z",
  updated_at: null,
};

function detail(kind: string, name: string) {
  render(
    <MainAgentProvider agents={[AGENT]}>
      <Pane>
        <ObjectDetail
          agentId={AGENT_ID}
          kind={kind}
          name={name}
          onOpen={() => {}}
          onBack={() => {}}
        />
      </Pane>
    </MainAgentProvider>,
  );
}

test("a notification is titled by its subject, in its row and in its record", async () => {
  wire({
    ["/objects/notification/" + NOTIFICATION_NAME]: () => json(NOTIFICATION_DETAIL),
    "/objects/notification": () => objectIndex(NOTIFICATION_KIND, [NOTIFICATION_ROW]),
  });
  mount([AGENT], "notification");

  await openRow(NOTIFICATION_SUBJECT);

  await waitFor(() => expect(standing()).toEqual([NOTIFICATION_SUBJECT]));
  expect(screen.queryByText(NOTIFICATION_NAME)).toBeNull();
  expect(headings()).not.toContain("Subject");
});

test("the turn that read a notification reads as a yes, and never as the turn's id", async () => {
  const TRIAGED_TURN = "7c3a1b2d-4e5f-4a6b-8c9d-0e1f2a3b4c5d";
  wire({
    ["/objects/notification/" + NOTIFICATION_NAME]: () =>
      json({
        ...NOTIFICATION_DETAIL,
        fields: ["subject", "triaged_turn"],
        status: { subject: NOTIFICATION_SUBJECT, triaged_turn: TRIAGED_TURN },
      }),
  });
  detail("notification", NOTIFICATION_NAME);

  expect(await waitFor(() => fact("Triaged"))).toBe("Yes");
  expect(screen.queryByText(TRIAGED_TURN)).toBeNull();
  expect(screen.queryByText("Triaged Turn")).toBeNull();
});

const PAGE_ID = "3f6c1d2b-7a8e-4c5f-9b0a-1d2e3f4a5b6c";
const PAGE_TITLE = "Weekly founder sync";
const MEMORY_NAME = "9c8b7a6d5e4f3a2b1c0d9e8f7a6b5c4d";
const MEMORY_TEXT = "The founders meet weekly on Monday.";

const CITED_MEMORY = {
  kind: "memory",
  fields: ["subject", "text", "created_from_page_id", "created_from_page_title"],
  spec_schema: null,
  applies: false,
  deletes: false,
  name: MEMORY_NAME,
  summary: MEMORY_TEXT,
  spec: null,
  status: {
    subject: "workspace",
    text: MEMORY_TEXT,
    created_from_page_id: PAGE_ID,
    created_from_page_title: PAGE_TITLE,
  },
  links: [{ relation: "created_from", kind: "page", name: PAGE_ID, opens: false }],
  created_at: "2026-07-01T09:00:00Z",
  updated_at: null,
};

test("a memory record is titled by its own words and names its page, not the page id", async () => {
  wire({ ["/objects/memory/" + MEMORY_NAME]: () => json(CITED_MEMORY) });
  detail("memory", MEMORY_NAME);

  await waitFor(() => expect(standing()).toEqual([MEMORY_TEXT]));
  expect(await screen.findByText("created_from page \u201c" + PAGE_TITLE + "\u201d")).toBeTruthy();
  expect(fact("Created From Page Title")).toBe(PAGE_TITLE);
  expect(screen.queryByText("Created From Page Id")).toBeNull();
  expect(screen.queryByText(PAGE_ID)).toBeNull();
  expect(screen.queryByText(MEMORY_NAME)).toBeNull();
});

test("a link to a record named by an id says what it links to and no more", async () => {
  wire({
    ["/objects/memory/" + MEMORY_NAME]: () =>
      json({
        ...CITED_MEMORY,
        status: { subject: "workspace", text: MEMORY_TEXT },
        links: [
          { relation: "superseded_by", kind: "memory", name: CONVO_ID, opens: false },
          { relation: "scoped_to", kind: "agent", name: "assistant", opens: false },
        ],
      }),
  });
  detail("memory", MEMORY_NAME);

  expect(await screen.findByText("superseded_by memory")).toBeTruthy();
  expect(screen.getByText("scoped_to agent assistant")).toBeTruthy();
  expect(screen.queryByText(CONVO_ID)).toBeNull();
});

test("a profile record states no member id, which no read here turns into words", async () => {
  const MEMBER_ID = "0a1b2c3d-4e5f-4a6b-8c9d-1e2f3a4b5c6d";
  wire({
    ["/objects/profile/" + MEMBER_ID]: () =>
      json({
        kind: "profile",
        fields: ["member_id", "role"],
        spec_schema: null,
        applies: false,
        deletes: false,
        name: MEMBER_ID,
        summary: "Founder, carrying the launch.",
        spec: null,
        status: { member_id: MEMBER_ID, role: "founder" },
        links: [],
        created_at: "2026-07-01T09:00:00Z",
        updated_at: null,
      }),
  });
  detail("profile", MEMBER_ID);

  expect(await waitFor(() => fact("Role"))).toBe("founder");
  expect(screen.queryByText("Member Id")).toBeNull();
});

test("a conversation record is titled by what the conversation is called", async () => {
  wire({
    ["/objects/conversation/" + CONVO_ID]: () =>
      json({
        ...CONVERSATION_DETAIL,
        fields: ["title", "surface"],
        status: { title: "Ship the nightly", surface: "web" },
      }),
  });
  detail("conversation", CONVO_ID);

  await waitFor(() => expect(standing()).toEqual(["Ship the nightly"]));
  expect(screen.queryByText(CONVO_ID)).toBeNull();
});

test("a conversation the wire titles with nothing falls back to its summary line", async () => {
  wire({ ["/objects/conversation/" + CONVO_ID]: () => json(CONVERSATION_DETAIL) });
  detail("conversation", CONVO_ID);

  await waitFor(() => expect(standing()).toEqual(["web conversation, created 2026-07-01"]));
  expect(screen.queryByText(CONVO_ID)).toBeNull();
});
