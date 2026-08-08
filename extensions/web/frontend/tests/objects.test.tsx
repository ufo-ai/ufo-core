import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { ObjectPane } from "@/kernel/objects";
import { relativeMoment } from "@/lib/moments";
import { MainAgentProvider } from "@/lib/mainAgent";

import {
  AGENT,
  AGENT_ID,
  CONVO_ID,
  MEMBER,
  NO_SITES,
  NO_SITE_OBJECTS,
  NO_TASKS,
  SECOND,
  SECOND_ID,
  SITE_KIND,
  TASK_KIND,
  json,
  objectIndex,
  refusedNotice,
  useStreamFake,
  wire,
} from "./harness";

const NOW = new Date("2026-08-01T12:00:00Z");
const IN_THREE_HOURS = new Date(Date.now() + 3 * 3_600_000).toISOString();

const TASK_ROW = {
  name: "daily-brief",
  summary: "0 9 * * * — daily brief",
  next_run_at: IN_THREE_HOURS,
  paused: false,
};

const SECOND_TASK_ROW = {
  name: "weekly-roll",
  summary: "0 9 * * 1 — weekly roll-up",
  next_run_at: IN_THREE_HOURS,
  paused: false,
};

const TASK_DETAIL = {
  ...TASK_KIND,
  name: "daily-brief",
  summary: "0 9 * * * — daily brief",
  spec: { schedule: "0 9 * * *", prompt: "write the daily brief", paused: false },
  status: { next_run_at: IN_THREE_HOURS, paused: false },
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
  const row = screen.getByRole("button", { name }).closest("tr");
  return [...(row?.querySelectorAll("td") ?? [])].map((box) => String(box.textContent));
}

function headings(): string[] {
  return screen.getAllByRole("columnheader").map((head) => String(head.textContent));
}

function mount(kind: string, label = "Tasks") {
  render(
    <MainAgentProvider agents={[AGENT]}>
      <ObjectPane agentId={AGENT_ID} kind={kind} label={label} />
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

test("an index is a table: one column per field the kind declares, headed by a member's word", async () => {
  wire({ "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW]) });
  mount("scheduled_task");

  await screen.findByRole("button", { name: "daily-brief" });
  expect(headings()).toEqual(["Name", "Summary", "Next Run At", "Paused", ""]);
  const said = cells("daily-brief");
  expect(said[0]).toBe("daily-brief");
  expect(said[1]).toBe("0 9 * * * — daily brief");
  expect(said[2]).toContain("in ");
  expect(said[3]).toBe("No");
});

test("a boolean cell answers its column, and a field the record lacks takes a dash", async () => {
  wire({
    "/objects/scheduled_task": () =>
      objectIndex(TASK_KIND, [{ ...TASK_ROW, paused: true, next_run_at: null }]),
  });
  mount("scheduled_task");

  await screen.findByRole("button", { name: "daily-brief" });
  expect(cells("daily-brief")[2]).toBe("—");
  expect(cells("daily-brief")[3]).toBe("Yes");
});

test("a value the kind's spec declares as an enum reads as its own chip", async () => {
  wire({
    "/objects/site": () =>
      objectIndex(SITE_KIND, [
        {
          name: "docs-abc",
          summary: "docs · workspace · sandbox port 3000",
          conversation: CONVO_ID,
          created_at: "2026-07-01T09:00:00Z",
          visibility: "workspace",
        },
      ]),
  });
  mount("site");

  await screen.findByRole("button", { name: "docs-abc" });
  expect(headings()).toEqual(["Name", "Summary", "Conversation", "Created At", "Visibility"]);
  const said = cells("docs-abc");
  expect(said[2]).toBe(CONVO_ID);
  expect(said[3]).toContain("ago");
  expect(said[4]).toBe("workspace");
});

test("an empty index states that the kind has no objects here", async () => {
  wire({ "/objects/site": () => objectIndex(SITE_KIND, []) });
  mount("site");

  expect(await screen.findByText(NO_SITE_OBJECTS)).toBeTruthy();
  expect(screen.queryByRole("table")).toBeNull();
});

test("the sites tab of a deploy without the extension states that, not an error", async () => {
  wire({
    "/objects/site": () => new Response("no object kind named 'site'", { status: 404 }),
    "/workspace/team": () => json({ members: [], can_add: false, domain: null }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/workspace/sites";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  expect(await screen.findByText("No sites extension is installed.")).toBeTruthy();
  expect(screen.queryByText(/^Error /)).toBeNull();
});

test("a kind the deploy always installs states the error it got, never an absence", async () => {
  wire({ "/objects/scheduled_task": () => new Response("no such agent", { status: 404 }) });
  mount("scheduled_task");

  expect(await screen.findByText("Error 404 — reload to retry.")).toBeTruthy();
  expect(screen.queryByText(NO_TASKS)).toBeNull();
});

test("a read that breaks still states the error, never an empty kind", async () => {
  wire({ "/objects/site": () => new Response("nope", { status: 503 }) });
  mount("site");

  expect(await screen.findByText("Error 503 — reload to retry.")).toBeTruthy();
  expect(screen.queryByText(NO_SITE_OBJECTS)).toBeNull();
});

test("ordering and a boolean filter ride the read, so the kind applies them", async () => {
  const reads: string[] = [];
  wire({
    "/objects/scheduled_task": (url) => {
      reads.push(url);
      return objectIndex(TASK_KIND, [TASK_ROW]);
    },
  });
  mount("scheduled_task");

  await screen.findByRole("button", { name: "daily-brief" });
  expect(reads[0]).toContain("order_by=name");

  await userEvent.click(screen.getByRole("button", { name: "Next Run At" }));
  await waitFor(() => expect(reads.at(-1)).toContain("order_by=next_run_at"));
  expect(reads.at(-1)).not.toContain("order=desc");

  await userEvent.click(screen.getByRole("button", { name: "Next Run At" }));
  await waitFor(() => expect(reads.at(-1)).toContain("order=desc"));

  await userEvent.click(screen.getByRole("tab", { name: "Paused" }));
  await waitFor(() => expect(reads.at(-1)).toContain("paused=true"));
});

test("a filter that narrows to nothing keeps the control that clears it", async () => {
  wire({
    "/objects/scheduled_task": (url) =>
      objectIndex(TASK_KIND, url.includes("paused=true") ? [] : [TASK_ROW]),
  });
  mount("scheduled_task");

  await screen.findByRole("button", { name: "daily-brief" });
  await userEvent.click(screen.getByRole("tab", { name: "Paused" }));

  expect(await screen.findByText("No scheduled task matches this search.")).toBeTruthy();
  expect(screen.queryByText(NO_TASKS)).toBeNull();
  expect(headings()).toEqual(["Name", "Summary", "Next Run At", "Paused", ""]);
  expect(screen.getByRole("tab", { name: "Paused" }).getAttribute("aria-selected")).toBe("true");

  await userEvent.click(screen.getByRole("tab", { name: "All" }));
  expect(await screen.findByRole("button", { name: "daily-brief" })).toBeTruthy();
});

test("what a member types rides the read as the kind's own search", async () => {
  const reads: string[] = [];
  wire({
    "/objects/scheduled_task": (url) => {
      reads.push(url);
      return objectIndex(TASK_KIND, url.includes("q=brief") ? [TASK_ROW] : []);
    },
  });
  mount("scheduled_task");

  await screen.findByText(NO_TASKS);
  await userEvent.type(screen.getByLabelText("Search scheduled task"), "brief{enter}");

  expect(await screen.findByRole("button", { name: "daily-brief" })).toBeTruthy();
  expect(reads.at(-1)).toContain("q=brief");

  await userEvent.clear(screen.getByLabelText("Search scheduled task"));
  await userEvent.type(screen.getByLabelText("Search scheduled task"), "nothing{enter}");

  expect(await screen.findByText("No scheduled task matches this search.")).toBeTruthy();
  expect(screen.queryByText(NO_TASKS)).toBeNull();
});

test("the order sits on the head of the column it orders, and only there", async () => {
  wire({ "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW]) });
  mount("scheduled_task");

  await screen.findByRole("button", { name: "daily-brief" });
  const heads = screen.getAllByRole("columnheader");
  expect(heads.map((head) => head.querySelector("button")?.textContent ?? null)).toEqual([
    "Name",
    "Summary",
    "Next Run At",
    "Paused",
    null,
  ]);
  expect(heads[0].getAttribute("aria-sort")).toBe("ascending");
  expect(heads[2].getAttribute("aria-sort")).toBe("none");

  await userEvent.click(screen.getByRole("button", { name: "Name" }));
  expect(screen.getAllByRole("columnheader")[0].getAttribute("aria-sort")).toBe("descending");
});

test("a page with more behind it walks on the cursor the kind returned", async () => {
  const reads: string[] = [];
  wire({
    "/objects/scheduled_task": (url) => {
      reads.push(url);
      return url.includes("cursor=c1")
        ? objectIndex(TASK_KIND, [SECOND_TASK_ROW])
        : objectIndex(TASK_KIND, [TASK_ROW], "c1");
    },
  });
  mount("scheduled_task");

  await screen.findByRole("button", { name: "daily-brief" });
  await userEvent.click(screen.getByRole("button", { name: "Next page" }));

  expect(await screen.findByRole("button", { name: "weekly-roll" })).toBeTruthy();
  expect(reads.at(-1)).toContain("cursor=c1");

  await userEvent.click(screen.getByRole("button", { name: "First page" }));

  expect(await screen.findByRole("button", { name: "daily-brief" })).toBeTruthy();
  expect(reads.at(-1)).not.toContain("cursor=");
});

test("a detail renders spec, then status, then links, then when the row was made", async () => {
  wire({
    "/objects/scheduled_task/daily-brief": () => json(TASK_DETAIL),
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW]),
  });
  mount("scheduled_task");

  await userEvent.click(await screen.findByRole("button", { name: "daily-brief" }));

  const sections = [...document.querySelectorAll("h2")].map((heading) => heading.textContent);
  expect(sections).toEqual(["daily-brief", "Spec", "Status", "Links"]);
  expect(document.querySelectorAll("h1").length).toBe(0);
  expect(await screen.findByText("write the daily brief")).toBeTruthy();
  expect(screen.getByText(/^Created /)).toBeTruthy();
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
  mount("scheduled_task");

  await userEvent.click(await screen.findByRole("button", { name: "daily-brief" }));
  const link = await screen.findByText("reports_to conversation " + CONVO_ID);
  await userEvent.click(link);

  expect(await screen.findByRole("heading", { name: CONVO_ID })).toBeTruthy();
  expect(screen.getAllByText("web").length).toBe(2);
  expect(screen.getByText("member:m1")).toBeTruthy();
  const closed = screen.getByText("scoped_to agent assistant");
  expect(closed.tagName).toBe("SPAN");
  expect(screen.queryByRole("button", { name: "scoped_to agent assistant" })).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  expect(await screen.findByRole("button", { name: "daily-brief" })).toBeTruthy();
});

test("an outcome stays on the row it happened to, not the next one opened", async () => {
  wire({
    ["/objects/conversation/" + CONVO_ID]: () => json(CONVERSATION_DETAIL),
    "/objects/scheduled_task/daily-brief": () => json(TASK_DETAIL),
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW]),
    "/intents": () => json({ applied: false, message: "The workspace refuses it." }),
  });
  mount("scheduled_task");

  await userEvent.click(await screen.findByRole("button", { name: "daily-brief" }));
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
  mount("scheduled_task");

  await userEvent.click(await screen.findByRole("button", { name: "daily-brief" }));

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
  mount("scheduled_task");

  await userEvent.click(await screen.findByRole("button", { name: "daily-brief" }));
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

test("the tasks tab of an agent is that agent's scheduled_task index", async () => {
  const reads: string[] = [];
  wire({
    "/objects/scheduled_task": (url) => {
      reads.push(url);
      return objectIndex(TASK_KIND, [TASK_ROW]);
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "/tasks";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  expect(await screen.findByRole("button", { name: "daily-brief" })).toBeTruthy();
  expect(reads[0]).toContain("agent=" + AGENT_ID);
});

test("landing on another agent's tasks leaves the first agent's detail behind", async () => {
  wire({
    "/objects/scheduled_task/daily-brief": () => json(TASK_DETAIL),
    "/objects/scheduled_task": (url) =>
      objectIndex(TASK_KIND, url.includes(AGENT_ID) ? [TASK_ROW] : [SECOND_TASK_ROW]),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "/tasks";
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} />);

  await userEvent.click(await screen.findByRole("button", { name: "daily-brief" }));
  await screen.findByRole("heading", { name: "daily-brief" });

  location.hash = "#/agents/" + SECOND_ID + "/tasks";
  window.dispatchEvent(new HashChangeEvent("hashchange"));

  expect(await screen.findByRole("button", { name: "weekly-roll" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "daily-brief" })).toBeNull();
});

test("the workspace sites tab is the site index on the main agent", async () => {
  const reads: string[] = [];
  wire({
    "/objects/site": (url) => {
      reads.push(url);
      return objectIndex(SITE_KIND, []);
    },
    "/workspace/team": () => json({ members: [], can_add: false, domain: null }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/workspace/sites";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  expect(await screen.findByText(NO_SITES)).toBeTruthy();
  expect(reads[0]).toContain("agent=" + AGENT_ID);
  expect(screen.queryByText(NO_TASKS)).toBeNull();
});

test("the act that writes an object opens over the index, and a refusal keeps it open", async () => {
  wire({
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW]),
    "/intents": () => json({ applied: false, message: "The workspace refuses it." }),
  });
  mount("scheduled_task");

  expect(await screen.findByRole("button", { name: "daily-brief" })).toBeTruthy();
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
  mount("scheduled_task");

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
  mount("scheduled_task");

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
  mount("scheduled_task");

  await userEvent.click(await screen.findByRole("button", { name: "daily-brief" }));
  await userEvent.click(await screen.findByRole("button", { name: "Edit" }));

  expect((await screen.findByLabelText("expires_at")).getAttribute("value")).toBe(LOCAL_EXPIRY);
});

test("a record is deleted from the row it stands on, and a refusal says so above the table", async () => {
  const posted: unknown[] = [];
  wire({
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, [TASK_ROW]),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: false, message: "The workspace refuses it." });
    },
  });
  mount("scheduled_task");

  await screen.findByRole("button", { name: "daily-brief" });
  await userEvent.click(screen.getByRole("button", { name: "Delete" }));
  expect(posted.length).toBe(0);
  await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({ verb: "delete", kind: "scheduled_task", name: "daily-brief" });
  await refusedNotice("The workspace refuses it.");
});

test("a kind the member cannot write offers no act on its rows", async () => {
  wire({ "/objects/site": () => objectIndex(SITE_KIND, [{ name: "docs-abc", summary: "docs" }]) });
  mount("site");

  await screen.findByRole("button", { name: "docs-abc" });
  expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
});
