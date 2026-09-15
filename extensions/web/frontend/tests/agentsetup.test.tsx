import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { AgentSetup, cronFor, labelOf } from "@/views/AgentSetup";
import { takePendingAsk } from "@/lib/pendingAsk";
import { TooltipProvider } from "@/components/ui/tooltip";

import { AGENT_ID, json, wire } from "./harness";

const SETUP = "/setup$";
const INTENTS = "/intents";
const TURN = "7f1d9d0e-6d2a-4c53-9d1f-2f4b9a0c1e77";

type FakeStream = {
  url: string;
  listeners: Record<string, (event: MessageEvent) => void>;
};

const APP = {
  id: AGENT_ID,
  name: "meetings",
  model: "auto",
  main: false,
  icon: "calendar",
  purpose: "Briefs every meeting before it starts.",
};

beforeEach(() => {
  vi.unstubAllGlobals();
});

function owed(over: Record<string, unknown> = {}) {
  return {
    connectors: [{
        provider: "googlecalendar",
        label: "Google Calendar",
        summary: "Brief what is on the calendar.",
        granted: false,
        required: true,
      }],
    credentials: [{ label: "Registry token", filled: false, required: false }],
    standing: [{ kind: "scheduled_task", armed: false, required: false, schedule: null }],
    schedule: {
      name: "meeting-briefs",
      prompt: "Brief each meeting starting in the next few hours.",
      cadences: [
        { hour: null, minute: 0, weekdays: [] },
        { hour: 9, minute: 0, weekdays: [1, 2, 3, 4, 5] },
      ],
    },
    instructions: "Connect the calendar.",
    ...over,
  };
}

/** `getTimezoneOffset` returns minutes to add to local time to reach UTC, so a positive number is west
 *  of Greenwich. */
function atOffset(minutes: number) {
  vi.spyOn(Date.prototype, "getTimezoneOffset").mockReturnValue(minutes);
}

function mount(onBuilt: () => void = () => {}) {
  render(
    <TooltipProvider>
      <AgentSetup agent={APP} onBuilt={onBuilt} />
    </TooltipProvider>,
  );
}

async function stepTrigger(name: string | RegExp) {
  const buttons = await screen.findAllByRole("button", { name });
  const trigger = buttons.find((button) => button.hasAttribute("aria-controls"));
  if (!trigger) throw new Error("step trigger not found");
  return trigger;
}

async function actButton(name: string | RegExp) {
  const buttons = await screen.findAllByRole("button", { name });
  const act = buttons.find((button) => !button.hasAttribute("aria-controls"));
  if (!act) throw new Error("step act not found");
  return act;
}

function applied(handler: ReturnType<typeof wire>["handler"]) {
  const posted = handler.mock.calls.find(([url]) => String(url).endsWith(INTENTS));
  return posted ? JSON.parse(String((posted[1] as RequestInit).body)) : null;
}

function fakeStream(): FakeStream[] {
  const opened: FakeStream[] = [];
  vi.stubGlobal(
    "EventSource",
    class {
      listeners: Record<string, (event: MessageEvent) => void> = {};
      constructor(public url: string) {
        opened.push(this as unknown as FakeStream);
      }
      addEventListener(name: string, handler: (event: MessageEvent) => void) {
        this.listeners[name] = handler;
      }
      close() {}
    },
  );
  return opened;
}

test("the screen states every need the app declares, with the act that settles it", async () => {
  wire({ [SETUP]: () => json(owed()) });
  mount();

  expect(await actButton("Connect Google Calendar")).toBeTruthy();
  await userEvent.click(await stepTrigger("Add the workspace credential"));
  expect(await screen.findByText("Not filled")).toBeTruthy();
  await userEvent.click(await stepTrigger("Choose when it runs"));
  expect(await screen.findByRole("radio", { name: /every weekday at 9:00 AM/ })).toBeTruthy();
  expect(await screen.findByLabelText("Something else")).toBeTruthy();
  expect(screen.queryByText("Connect the calendar.")).toBeNull();
});

test("the first unfinished step is open on arrival", async () => {
  wire({
    [SETUP]: () =>
      json(
        owed({
          connectors: [{
            provider: "googlecalendar",
            label: "Google Calendar",
            summary: "Brief what is on the calendar.",
            granted: true,
            required: true,
          }],
        }),
      ),
  });
  mount();

  expect((await stepTrigger(/Choose your account/)).getAttribute("aria-expanded")).toBe("false");
  expect((await stepTrigger("Add the workspace credential")).getAttribute("aria-expanded")).toBe(
    "true",
  );
});

test("a done step draws the filled check mark", async () => {
  wire({
    [SETUP]: () =>
      json(
        owed({
          connectors: [{
            provider: "googlecalendar",
            label: "Google Calendar",
            summary: "Brief what is on the calendar.",
            granted: true,
            required: true,
          }],
        }),
      ),
  });
  mount();

  const trigger = await stepTrigger(/Choose your account/);
  expect(trigger.querySelector(".tabler-icon-square-rounded-check-filled")).toBeTruthy();
});

test("required steps draw an asterisk and optional steps do not", async () => {
  wire({ [SETUP]: () => json(owed()) });
  mount();

  expect((await stepTrigger(/Choose your account/)).textContent).toContain(
    "Choose your account*",
  );
  expect((await stepTrigger("Add the workspace credential")).textContent).not.toContain("*");
});

test("a credential step states whether the workspace has filled it", async () => {
  wire({ [SETUP]: () => json(owed()) });
  mount();

  await userEvent.click(await stepTrigger("Add the workspace credential"));
  expect(
    await screen.findByText("Registry token is filled once for the whole workspace."),
  ).toBeTruthy();
  expect(await screen.findByText("Not filled")).toBeTruthy();
  expect(screen.queryByText("Filled")).toBeNull();
  cleanup();

  wire({
    [SETUP]: () =>
      json(owed({ credentials: [{ label: "Registry token", filled: true, required: false }] })),
  });
  mount();

  const trigger = await stepTrigger("Add the workspace credential");
  expect(trigger.querySelector(".tabler-icon-square-rounded-check-filled")).toBeTruthy();
  await userEvent.click(trigger);
  expect(await screen.findByText("Filled")).toBeTruthy();
  expect(screen.queryByText("Not filled")).toBeNull();
});

test("a completed open step advances to the next step in the payload", async () => {
  const opened = { location: { href: "" }, close: vi.fn(), focus: vi.fn() };
  vi.stubGlobal("open", vi.fn().mockReturnValue(opened));
  const streams = fakeStream();
  let reads = 0;
  wire({
    [SETUP]: () => {
      reads += 1;
      return json(
        owed(
          reads === 1
            ? {}
            : {
                connectors: [{
                  provider: "googlecalendar",
                  label: "Google Calendar",
                  summary: "Brief what is on the calendar.",
                  granted: true,
                  required: true,
                }],
              },
        ),
      );
    },
    [INTENTS]: () => json({ applied: true, message: "", turn_id: TURN }),
  });
  mount();

  expect((await stepTrigger(/Choose your account/)).getAttribute("aria-expanded")).toBe("true");
  await userEvent.click(await actButton("Connect Google Calendar"));
  await waitFor(() => expect(streams.length).toBe(1));
  streams[0].listeners.connect(new MessageEvent("connect"));

  await waitFor(async () => {
    expect((await stepTrigger("Add the workspace credential")).getAttribute("aria-expanded")).toBe(
      "true",
    );
  });
});

test("settling the last step hands the drawer back to the first one outstanding", async () => {
  let reads = 0;
  wire({
    [SETUP]: () => {
      reads += 1;
      return json(
        owed(
          reads === 1
            ? {}
            : { standing: [{ kind: "scheduled_task", armed: true, required: false, schedule: "0 9 * * 1,2,3,4,5" }] },
        ),
      );
    },
    [INTENTS]: () => json({ applied: true, message: "" }),
  });
  mount();

  await userEvent.click(await stepTrigger("Choose when it runs"));
  await userEvent.click(await screen.findByRole("radio", { name: /every hour/ }));

  await waitFor(async () => {
    expect((await stepTrigger("Choose when it runs")).getAttribute("aria-expanded")).toBe("false");
  });
  expect((await stepTrigger(/Choose your account/)).getAttribute("aria-expanded")).toBe("true");
});

test("a settled schedule keeps the offer it took, checked", async () => {
  atOffset(0);
  wire({
    [SETUP]: () =>
      json(
        owed({
          standing: [
            {
              kind: "scheduled_task",
              armed: true,
              required: false,
              schedule: "0 9 * * 1,2,3,4,5",
            },
          ],
        }),
      ),
  });
  mount();

  await userEvent.click(await stepTrigger("Choose when it runs"));
  const taken = await screen.findByRole("radio", { name: /every weekday at 9:00 AM/ });
  expect(taken.getAttribute("aria-checked")).toBe("true");
  expect(screen.queryByText("Set")).toBeNull();
});

test("a schedule the member composed stands in the field they wrote it in", async () => {
  atOffset(0);
  wire({
    [SETUP]: () =>
      json(
        owed({
          standing: [
            { kind: "scheduled_task", armed: true, required: false, schedule: "0 6 * * 2" },
          ],
        }),
      ),
  });
  mount();

  await userEvent.click(await stepTrigger("Choose when it runs"));
  expect((await screen.findByLabelText("Something else")).getAttribute("value")).toBe(
    "Tuesday at 6:00 AM",
  );
});

test("a need the app offers no cadence for still carries an act", async () => {
  wire({
    [SETUP]: () =>
      json(
        owed({
          standing: [{ kind: "source_trigger", armed: false, required: false, schedule: null }],
          schedule: null,
        }),
      ),
  });
  mount();

  await userEvent.click(await stepTrigger("Choose what wakes it"));
  await userEvent.click(await actButton("Set up"));

  expect(location.hash).toBe("#/new/" + AGENT_ID);
  expect(takePendingAsk(AGENT_ID, "new:" + AGENT_ID)).toEqual({
    text: "Connect the calendar.",
    send: false,
    meant: null,
    starter: null,
  });
});

test("Build app stands below the todos and hands the ask over unsent", async () => {
  wire({ [SETUP]: () => json(owed()) });
  mount();

  await userEvent.click(await screen.findByRole("button", { name: "Build app" }));

  expect(location.hash).toBe("#/new/" + AGENT_ID);
  const handed = takePendingAsk(AGENT_ID, "new:" + AGENT_ID);
  expect(handed?.send).toBe(false);
  expect(handed?.text).toContain("Load your homepage skill");
  expect((handed?.text ?? "").length).toBeLessThan(160);
});

test("connect opens the consent window on the press, and points it at the turn's own link", async () => {
  const opened = { location: { href: "" }, close: vi.fn(), focus: vi.fn() };
  vi.stubGlobal("open", vi.fn().mockReturnValue(opened));
  const streams = fakeStream();
  wire({
    [SETUP]: () => json(owed()),
    [INTENTS]: () => json({ applied: true, message: "", turn_id: TURN }),
  });
  mount();

  await userEvent.click(await actButton("Connect Google Calendar"));
  await waitFor(() => expect(streams.length).toBe(1));
  expect(streams[0].url).toContain("/turns/" + TURN + "/stream");

  streams[0].listeners.connect(new MessageEvent("connect"));
  expect(opened.location.href).toContain("/turns/" + TURN + "/connect");
});

test("a turn that mints no link says so, and gives the controls back", async () => {
  const opened = { location: { href: "" }, close: vi.fn(), focus: vi.fn() };
  vi.stubGlobal("open", vi.fn().mockReturnValue(opened));
  const streams = fakeStream();
  wire({
    [SETUP]: () => json(owed()),
    [INTENTS]: () => json({ applied: true, message: "", turn_id: TURN }),
  });
  mount();

  await userEvent.click(await actButton("Connect Google Calendar"));
  await waitFor(() => expect(streams.length).toBe(1));
  streams[0].listeners.terminal(new MessageEvent("terminal"));

  expect(
    await screen.findByText("The connect did not open a consent page. Try again."),
  ).toBeTruthy();
  expect(opened.close).toHaveBeenCalled();
  expect(await actButton("Connect Google Calendar")).toBeTruthy();
});

test("the pick applies the app's own schedule, under the app's own name", async () => {
  atOffset(0);
  const { handler } = wire({
    [SETUP]: () => json(owed()),
    [INTENTS]: () => json({ applied: true, message: "Saved." }),
  });
  mount();

  await userEvent.click(await stepTrigger("Choose when it runs"));
  await userEvent.click(await screen.findByRole("radio", { name: /every weekday at 9:00 AM/ }));

  await waitFor(() => expect(applied(handler)).toBeTruthy());
  expect(applied(handler)).toEqual({
    verb: "apply",
    kind: "scheduled_task",
    name: "meeting-briefs",
    spec: {
      schedule: "0 9 * * 1,2,3,4,5",
      prompt: "Brief each meeting starting in the next few hours.",
    },
  });
});

test("a re-pick on an armed schedule moves the cadence and nothing else", async () => {
  atOffset(0);
  const { handler } = wire({
    [SETUP]: () =>
      json(
        owed({
          standing: [
            {
              kind: "scheduled_task",
              armed: true,
              required: false,
              schedule: "0 9 * * 1,2,3,4,5",
            },
          ],
        }),
      ),
    [INTENTS]: () => json({ applied: true, message: "Saved." }),
  });
  mount();

  await userEvent.click(await stepTrigger("Choose when it runs"));
  await userEvent.click(await screen.findByRole("radio", { name: /every hour/ }));

  await waitFor(() => expect(applied(handler)).toBeTruthy());
  expect(applied(handler)).toEqual({
    verb: "apply",
    kind: "scheduled_task",
    name: "meeting-briefs",
    spec: { schedule: "0 * * * *" },
  });
});

test("a cadence label names the recurrence and promises no run now", () => {
  // A schedule's first fire is its next cron occurrence: `next_fire` advances strictly past now, and the
  // kind runs nothing one-shot.
  expect(labelOf({ hour: null, minute: 0, weekdays: [] })).toBe("every hour");
  expect(labelOf({ hour: 9, minute: 0, weekdays: [1, 2, 3, 4, 5] })).toBe("every weekday at 9:00 AM");
  expect(labelOf({ hour: 9, minute: 0, weekdays: [] })).toBe("every day at 9:00 AM");
});

test("the cadence is stored in UTC, converted from the member's own clock", () => {
  expect(cronFor({ hour: 9, minute: 0, weekdays: [] }, 0)).toBe("0 9 * * *");
  expect(cronFor({ hour: 9, minute: 0, weekdays: [] }, 300)).toBe("0 14 * * *");
  expect(cronFor({ hour: 21, minute: 0, weekdays: [1] }, 300)).toBe("0 2 * * 2");
  expect(cronFor({ hour: null, minute: 0, weekdays: [] }, 300)).toBe("0 * * * *");
});

test("the screen says when the workspace has built the page, and stays quiet until it has", async () => {
  wire({ [SETUP]: () => json(owed()) });
  const built = vi.fn();
  mount(built);

  expect(await actButton("Connect Google Calendar")).toBeTruthy();
  expect(built).not.toHaveBeenCalled();
});

test("a page the workspace has built is stated once", async () => {
  wire({ [SETUP]: () => json(owed({ own_page: true })) });
  const built = vi.fn();
  mount(built);

  await waitFor(() => expect(built).toHaveBeenCalled());
});
