import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { ObjectDetail } from "@/kernel/objects";
import { Pane } from "@/kernel/pane";
import { railFounded } from "@/lib/railStore";
import { Viewer } from "@/lib/audience";
import { chatHash } from "@/lib/route";
import { MainAgentProvider } from "@/lib/mainAgent";

import { AGENT, AGENT_ID, CONVO_ID, MEMBER, json, useStreamFake, wire } from "./harness";

const AFTER_SETTLE = { timeout: 3000 };

const FUTURE_EXPIRY = new Date(Date.now() + 24 * 3_600_000)
  .toISOString()
  .slice(0, "YYYY-MM-DDTHH:MM".length);

const TASK = {
  kind: "scheduled_task",
  fields: ["conversation", "next_run_at", "origin", "owner_email", "paused", "prompt"],
  spec_schema: {
    properties: {
      schedule: { type: "string" },
      prompt: { type: "string", title: "Prompt" },
      description: { type: "string" },
      expires_at: { anyOf: [{ type: "string", format: "date-time" }, { type: "null" }] },
      paused: { type: "boolean" },
      run_now: { type: "boolean" },
    },
  },
  applies: true,
  deletes: true,
  name: "daily-brief",
  summary: "0 9 * * * — daily brief",
  spec: {
    schedule: "30 3 * * *",
    prompt: "write the daily brief",
    description: "the morning digest",
    paused: false,
  },
  status: {
    next_run_at: "2026-08-27T03:30:00Z",
    last_run_at: "2026-08-26T03:30:00Z",
    last_run_status: "done",
    paused: false,
    mine: true,
    content_editable: true,
    schedule_editable: true,
    pausable: true,
    resumable: true,
    runnable: true,
    deletable: true,
    origin: "#general",
    owner_email: MEMBER.email,
  },
  links: [{ relation: "reports_to", kind: "conversation", name: CONVO_ID, opens: true }],
  created_at: "2026-07-01T09:00:00Z",
  updated_at: "2026-07-02T09:00:00Z",
};

function taskOnWire(): { posted: Record<string, unknown>[]; task: () => typeof TASK } {
  const posted: Record<string, unknown>[] = [];
  let held = TASK;
  wire({
    "/objects/scheduled_task/daily-brief": () => json(held),
    "/intents": (_url, init) => {
      const envelope = JSON.parse(String(init?.body));
      posted.push(envelope);
      held = { ...held, spec: { ...held.spec, ...envelope.spec } };
      return json({ applied: true, message: "Applied." });
    },
  });
  return { posted, task: () => held };
}

function mount(agents = [AGENT]) {
  render(
    <Viewer.Provider value={MEMBER.email}>
      <MainAgentProvider agents={agents}>
        <Pane>
          <ObjectDetail
            agentId={AGENT_ID}
            kind="scheduled_task"
            name="daily-brief"
            onOpen={() => {}}
            onBack={() => {}}
          />
        </Pane>
      </MainAgentProvider>
    </Viewer.Provider>,
  );
}

async function raise(name: string): Promise<HTMLElement> {
  await userEvent.click(await screen.findByRole("button", { name }));
  return screen.findByRole("menu");
}

beforeEach(() => {
  useStreamFake();
});

test("the prompt is typed in the pane itself, and the pane carries no submit", async () => {
  const { posted } = taskOnWire();
  mount();

  const prompt = await screen.findByLabelText("Prompt");
  expect((prompt as HTMLTextAreaElement).value).toBe("write the daily brief");
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();

  fireEvent.change(prompt, { target: { value: "write the evening brief" } });
  fireEvent.blur(prompt);

  await waitFor(() =>
    expect(posted).toEqual([
      {
        verb: "apply",
        kind: "scheduled_task",
        name: "daily-brief",
        spec: { prompt: "write the evening brief" },
      },
    ]),
  );
  expect(await screen.findByText("Saved")).toBeTruthy();
});

test("a prompt left alone saves itself once the typing settles", async () => {
  const { posted } = taskOnWire();
  mount();

  fireEvent.change(await screen.findByLabelText("Prompt"), { target: { value: "post the roll" } });

  await waitFor(() => expect(posted.length).toBe(1), AFTER_SETTLE);
  expect(posted[0]).toMatchObject({ spec: { prompt: "post the roll" } });
});

test("the schedule pill names the cadence and writes the cron the pick stands for", async () => {
  const { posted } = taskOnWire();
  mount();

  expect(await screen.findByRole("button", { name: "Schedule" })).toHaveProperty(
    "textContent",
    "Daily at 9:00 AM",
  );

  const menu = await raise("Schedule");
  expect([...menu.querySelectorAll("[role=menuitemradio]")].map((one) => one.textContent)).toEqual([
    "Interval",
    "Daily",
    "Weekdays",
    "Weekly",
    "Monthly",
    "Custom",
  ]);
  expect(
    menu.querySelector("[role=menuitemradio][aria-checked=true]")?.textContent,
  ).toBe("Daily");

  await userEvent.click(within(menu).getByRole("menuitemradio", { name: "Weekdays" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({ spec: { schedule: "30 3 * * 1,2,3,4,5" } });
});

test("the time box applies the hour the typing settles on, not every hour typed through", async () => {
  const { posted } = taskOnWire();
  mount();

  const time = await screen.findByLabelText("Time");
  expect(time).toHaveProperty("value", "09:00");
  fireEvent.change(time, { target: { value: "01:00" } });
  fireEvent.change(time, { target: { value: "14:00" } });

  await waitFor(() => expect(posted.length).toBe(1), AFTER_SETTLE);
  expect(posted[0]).toMatchObject({ spec: { schedule: "30 8 * * *" } });
});

test("an interval cadence offers the hours it fires on, and nothing that divides unevenly", async () => {
  const { posted } = taskOnWire();
  mount();

  await userEvent.click(
    within(await raise("Schedule")).getByRole("menuitemradio", { name: "Interval" }),
  );
  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({ spec: { schedule: "0 * * * *" } });

  const hours = await raise("Interval");
  expect([...hours.querySelectorAll("[role=menuitemradio]")].map((one) => one.textContent)).toEqual([
    "Every hour",
    "Every 2 hours",
    "Every 3 hours",
    "Every 4 hours",
    "Every 6 hours",
    "Every 8 hours",
    "Every 12 hours",
  ]);

  await userEvent.click(within(hours).getByRole("menuitemradio", { name: "Every 6 hours" }));
  await waitFor(() => expect(posted.length).toBe(2));
  expect(posted[1]).toMatchObject({ spec: { schedule: "0 */6 * * *" } });
});

test("the Custom pick opens the cron box, and the cron typed there is what changes the schedule", async () => {
  const { posted } = taskOnWire();
  mount();

  await userEvent.click(
    within(await raise("Schedule")).getByRole("menuitemradio", { name: "Custom" }),
  );

  const cron = await screen.findByLabelText("Cron (UTC)");
  expect(cron).toHaveProperty("value", "30 3 * * *");
  expect(posted).toEqual([]);
  expect(await screen.findByRole("button", { name: "Schedule" })).toHaveProperty(
    "textContent",
    "30 3 * * *",
  );

  fireEvent.change(cron, { target: { value: "15 2,14 * * *" } });
  fireEvent.blur(cron);

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({ spec: { schedule: "15 2,14 * * *" } });
});

test("a pick to Custom and back keeps the time the task already fires at", async () => {
  const posted: Record<string, unknown>[] = [];
  wire({
    "/objects/scheduled_task/daily-brief": () =>
      json({ ...TASK, spec: { ...TASK.spec, schedule: "0 6 * * *" } }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Applied." });
    },
  });
  mount();

  expect(await screen.findByRole("button", { name: "Schedule" })).toHaveProperty(
    "textContent",
    "Daily at 11:30 AM",
  );

  await userEvent.click(
    within(await raise("Schedule")).getByRole("menuitemradio", { name: "Custom" }),
  );
  expect(posted).toEqual([]);

  await userEvent.click(
    within(await raise("Schedule")).getByRole("menuitemradio", { name: "Daily" }),
  );

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({ spec: { schedule: "0 6 * * *" } });
});

test("a cron no offer stands for reads as custom, and is typed as the cron itself", async () => {
  const { posted } = taskOnWire();
  wire({
    "/objects/scheduled_task/daily-brief": () =>
      json({ ...TASK, spec: { ...TASK.spec, schedule: "15 2,14 * * *" } }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Applied." });
    },
  });
  mount();

  expect(await screen.findByRole("button", { name: "Schedule" })).toHaveProperty(
    "textContent",
    "15 2,14 * * *",
  );
  const cron = await screen.findByLabelText("Cron (UTC)");
  fireEvent.change(cron, { target: { value: "0 5 * * 1" } });
  fireEvent.blur(cron);

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({ spec: { schedule: "0 5 * * 1" } });
});

test("the chat the task reports into is stated and pressed through, never picked", async () => {
  const opened: unknown[] = [];
  taskOnWire();
  render(
    <MainAgentProvider agents={[AGENT]}>
      <Pane>
        <ObjectDetail
          agentId={AGENT_ID}
          kind="scheduled_task"
          name="daily-brief"
          onOpen={(at) => opened.push(at)}
          onBack={() => {}}
        />
      </Pane>
    </MainAgentProvider>,
  );

  await userEvent.click(await screen.findByRole("button", { name: "Reports to #general" }));

  expect(opened).toEqual([{ agent: AGENT_ID, kind: "conversation", name: CONVO_ID }]);
  expect(screen.queryByRole("button", { name: "New chat" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Notifications" })).toBeNull();
});

test("the conversation the task belongs to stands in the pane by its title", async () => {
  wire({
    "/objects/scheduled_task/daily-brief": () =>
      json({ ...TASK, status: { ...TASK.status, conversation: CONVO_ID } }),
  });
  railFounded({
    conversation_id: CONVO_ID,
    agent_id: AGENT_ID,
    agent_name: "assistant",
    title: "Ship the nightly",
    last_at: "2026-08-01T09:00:00.000Z",
    surface: "web",
    surface_label: null,
    audience: "member:m1",
    member_email: "member@example.com",
    mine: true,
    speaker: null,
  });
  mount();

  const link = await screen.findByRole("link", { name: "Ship the nightly" });
  expect(link.getAttribute("href")).toBe(chatHash(CONVO_ID));
  expect(screen.queryByText(CONVO_ID)).toBeNull();
});

test("a conversation the rail has no title for reads as the plain word, never as its id", async () => {
  wire({
    "/objects/scheduled_task/daily-brief": () =>
      json({ ...TASK, status: { ...TASK.status, conversation: CONVO_ID } }),
  });
  mount();

  const link = await screen.findByRole("link", { name: "Conversation" });
  expect(link.getAttribute("href")).toBe(chatHash(CONVO_ID));
  expect(screen.queryByText(CONVO_ID)).toBeNull();
});

test("a frame the browser denies storage still draws the link, rather than tearing the page down", async () => {
  const held = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    get() {
      throw new DOMException("storage is blocked", "SecurityError");
    },
  });
  try {
    wire({
      "/objects/scheduled_task/daily-brief": () =>
        json({ ...TASK, status: { ...TASK.status, conversation: CONVO_ID } }),
    });
    mount();

    const link = await screen.findByRole("link", { name: "Conversation" });
    expect(link.getAttribute("href")).toBe(chatHash(CONVO_ID));
  } finally {
    if (held) Object.defineProperty(globalThis, "localStorage", held);
  }
});

test("a conversation the wire names with a projection alone is titled by its description", async () => {
  wire({
    "/objects/scheduled_task/daily-brief": () =>
      json({ ...TASK, status: { ...TASK.status, conversation: CONVO_ID } }),
    "/api/chats": () =>
      json({
        chats: [],
        conversation: {
          id: CONVO_ID,
          agent: { id: AGENT_ID, name: "assistant", icon: "spark" },
          surface: "slack",
          surface_label: "#general",
          audience: "member:m1",
          member_email: MEMBER.email,
          description: "Ship the nightly",
          source: null,
          speakers: [MEMBER.email],
          turn_count: 1,
          created_at: "2026-07-30T10:00:00",
          last_turn_at: "2026-07-30T10:00:01",
          readable: true,
          disclosable: false,
          commentable: false,
        },
      }),
  });
  mount();

  const link = await screen.findByRole("link", { name: "Ship the nightly" });
  expect(link.getAttribute("href")).toBe(chatHash(CONVO_ID));
});

test("a conversation the rail never gathered is sought, so an app page names it too", async () => {
  wire({
    "/objects/scheduled_task/daily-brief": () =>
      json({ ...TASK, status: { ...TASK.status, conversation: CONVO_ID } }),
    "/api/chats": () =>
      json({
        chats: [
          {
            conversation_id: CONVO_ID,
            agent_id: AGENT_ID,
            agent_name: "assistant",
            title: "Ship the nightly",
            last_at: "2026-08-01T09:00:00.000Z",
            surface: "web",
            surface_label: null,
            mine: true,
            speaker: null,
          },
        ],
      }),
  });
  mount();

  const link = await screen.findByRole("link", { name: "Ship the nightly" });
  expect(link.getAttribute("href")).toBe(chatHash(CONVO_ID));
});

test("the runs the task has had stand beside the acts that fire and stop it", async () => {
  const { posted } = taskOnWire();
  mount();

  await userEvent.click(await screen.findByRole("button", { name: "Run now" }));
  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({ spec: { run_now: true } });

  await userEvent.click(screen.getByRole("button", { name: "Pause" }));
  await waitFor(() => expect(posted.length).toBe(2));
  expect(posted[1]).toMatchObject({ spec: { paused: true } });
  expect(await screen.findByRole("button", { name: "Resume" })).toBeTruthy();
});

test("run now on a paused task resumes it, so the fire it asks for can be claimed", async () => {
  const posted: Record<string, unknown>[] = [];
  wire({
    "/objects/scheduled_task/daily-brief": () =>
      json({
        ...TASK,
        spec: { ...TASK.spec, paused: true },
        status: { ...TASK.status, paused: true },
      }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Applied." });
    },
  });
  mount();

  await userEvent.click(await screen.findByRole("button", { name: "Resume and run now" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({ spec: { run_now: true, paused: false } });
});

test("a task somebody else wrote states its prompt and refuses the keystroke", async () => {
  wire({
    "/objects/scheduled_task/daily-brief": () =>
      json({
        ...TASK,
        status: {
          ...TASK.status,
          mine: false,
          content_editable: false,
          schedule_editable: false,
          pausable: false,
          resumable: false,
          runnable: false,
          deletable: false,
          owner_email: "mel@example.com",
        },
      }),
  });
  mount();

  const prompt = await screen.findByLabelText("Prompt");
  expect(prompt).toHaveProperty("readOnly", true);
  expect(
    await screen.findByText("Only the member who wrote this task can change what it says."),
  ).toBeTruthy();
  expect((await screen.findByRole("button", { name: "Schedule" })).hasAttribute("disabled")).toBe(
    true,
  );
  expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
});

test("a task whose prompt the reader may not see states its summary instead", async () => {
  wire({
    "/objects/scheduled_task/daily-brief": () =>
      json({ ...TASK, spec: null, summary: "0 9 * * * — private member task" }),
  });
  mount();

  expect(await screen.findByText(/private member task/)).toBeTruthy();
  expect(screen.queryByLabelText("Prompt")).toBeNull();
  expect(screen.getByRole("button", { name: "Delete" })).toBeTruthy();
});

test("an admin may stop or delete a private task without opening its content", async () => {
  wire({
    "/objects/scheduled_task/daily-brief": () =>
      json({
        ...TASK,
        spec: null,
        summary: "0 9 * * * — private member task",
        status: {
          ...TASK.status,
          mine: false,
          content_editable: false,
          schedule_editable: false,
          pausable: true,
          resumable: false,
          runnable: false,
          deletable: true,
          owner_email: "mel@example.com",
        },
      }),
  });
  mount();

  expect(await screen.findByText(/private member task/)).toBeTruthy();
  expect(screen.queryByLabelText("Prompt")).toBeNull();
  expect(screen.getByRole("button", { name: "Pause" })).toHaveProperty("disabled", false);
  expect(screen.getByRole("button", { name: "Delete" })).toBeTruthy();
});

test("an expiry typed on the member's clock is applied as an instant", async () => {
  const { posted } = taskOnWire();
  mount();

  expect(await screen.findByLabelText("Expires")).toHaveProperty("value", "");
  fireEvent.change(screen.getByLabelText("Expires"), {
    target: { value: FUTURE_EXPIRY },
  });

  await waitFor(() => expect(posted.length).toBe(1), AFTER_SETTLE);
  expect(posted[0]).toMatchObject({
    spec: { expires_at: new Date(FUTURE_EXPIRY).toISOString() },
  });
});

test("the expires box applies the moment the typing settles on, not every value passed through", async () => {
  const { posted } = taskOnWire();
  mount();

  const box = await screen.findByLabelText("Expires");
  fireEvent.change(box, { target: { value: "0002-09-01T09:00" } });
  fireEvent.change(box, { target: { value: "0202-09-01T09:00" } });
  fireEvent.change(box, { target: { value: FUTURE_EXPIRY } });

  await waitFor(() => expect(posted.length).toBe(1), AFTER_SETTLE);
  expect(posted[0]).toMatchObject({
    spec: { expires_at: new Date(FUTURE_EXPIRY).toISOString() },
  });
});

test("an expires box that holds a past moment applies nothing", async () => {
  const { posted } = taskOnWire();
  mount();

  const box = await screen.findByLabelText("Expires");
  fireEvent.change(box, { target: { value: "0002-09-01T09:00" } });
  await waitFor(() => expect(posted.length).toBe(0), { timeout: 3000 });
  fireEvent.blur(box);

  await new Promise((done) => setTimeout(done, 300));
  expect(posted.length).toBe(0);
});

test("an emptied expires box clears the expiry the task holds", async () => {
  const posted: { spec: Record<string, unknown> }[] = [];
  const expires = new Date("2026-09-01T09:00").toISOString();
  let held = { ...TASK, spec: { ...TASK.spec, expires_at: expires } };
  wire({
    "/objects/scheduled_task/daily-brief": () => json(held),
    "/intents": (_url, init) => {
      const envelope = JSON.parse(String(init?.body));
      posted.push(envelope);
      held = { ...held, spec: { ...held.spec, ...envelope.spec } };
      return json({ applied: true, message: "Applied." });
    },
  });
  mount();

  const box = await screen.findByLabelText("Expires");
  expect(box).toHaveProperty("value", "2026-09-01T09:00");
  fireEvent.change(box, { target: { value: "" } });
  fireEvent.blur(box);

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0].spec.expires_at).toBeNull();
  await waitFor(() => expect(screen.getByLabelText("Expires")).toHaveProperty("value", ""));
});

test("an expiry off the wire is stated on the clock in front of the member", async () => {
  wire({
    "/objects/scheduled_task/daily-brief": () =>
      json({
        ...TASK,
        spec: { ...TASK.spec, expires_at: new Date("2026-09-01T09:00").toISOString() },
      }),
  });
  mount();

  expect(await screen.findByLabelText("Expires")).toHaveProperty("value", "2026-09-01T09:00");
});
