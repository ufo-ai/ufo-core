import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { AgentSetup, cronFor, labelOf } from "@/views/AgentSetup";
import { takePendingAsk } from "@/lib/pendingAsk";
import { TooltipProvider } from "@/components/ui/tooltip";

import { AGENT_ID, json, wire } from "./harness";

const SETUP = "/setup$";
const INTENTS = "/intents";
const GITHUB_INSTALL =
  "/agents/" + AGENT_ID + "/actions/credential/github-app-installation/connect_github";
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

/** One app's whole declaration, unwired: an account its member grants, a credential a workspace
 *  install fills, and the standing order that gives it an occasion to run. */
function owed(over: Record<string, unknown> = {}) {
  return {
    connectors: [{
        provider: "googlecalendar",
        label: "Google Calendar",
        summary: "Brief what is on the calendar.",
        granted: false,
        required: true,
      }],
    credentials: [
      { label: "ufo GitHub App", filled: false, provider: "github", required: false },
    ],
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

/** Pin the member's clock. The offset is minutes to add to local time to reach UTC, so a positive
 *  number is west of Greenwich. */
function atOffset(minutes: number) {
  vi.spyOn(Date.prototype, "getTimezoneOffset").mockReturnValue(minutes);
}

function mount(admin = true, onBuilt: () => void = () => {}) {
  render(
    <TooltipProvider>
      <AgentSetup agent={APP} admin={admin} onBuilt={onBuilt} />
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

function applied(handler: ReturnType<typeof wire>["handler"], lane = INTENTS) {
  const posted = handler.mock.calls.find(([url]) => String(url).endsWith(lane));
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
  await userEvent.click(await stepTrigger("Install the workspace app"));
  expect(await actButton("Install ufo GitHub App")).toBeTruthy();
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
  expect((await stepTrigger("Install the workspace app")).getAttribute("aria-expanded")).toBe("true");
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
  expect((await stepTrigger("Install the workspace app")).textContent).not.toContain("*");
});

test("a workspace install is one press, and its link opens the provider's own page", async () => {
  /** A credential is filled once for the whole workspace, and the named tool mints the link inside
   *  its own turn — so unlike a brokered grant the outcome carries the URL straight back. Without
   *  this the row stated a need whose only settlement was a verb the member had to find in chat. */
  const opened = { location: { href: "" }, close: vi.fn(), focus: vi.fn() };
  vi.stubGlobal("open", vi.fn().mockReturnValue(opened));
  const { handler } = wire({
    [SETUP]: () => json(owed()),
    [GITHUB_INSTALL]: () => json({ applied: true, message: "", url: "https://github.test/install" }),
    "/actions/credential/github-app-installation": () =>
      json({
        actions: [
          {
            name: "connect_github",
            description: "Install the ufo GitHub App.",
            input_schema: { properties: {} },
            call: {
              kind: "credential",
              action: "connect_github",
              name: "github-app-installation",
              input: {},
            },
            label: "Connect",
          },
        ],
      }),
  });
  mount();

  await userEvent.click(await stepTrigger("Install the workspace app"));
  await userEvent.click(await actButton("Install ufo GitHub App"));

  await waitFor(() => expect(applied(handler, GITHUB_INSTALL)).toEqual({}));
  expect(opened.location.href).toBe("https://github.test/install");
});

test("a completed open step advances to the next step in the payload", async () => {
  const opened = { location: { href: "" }, close: vi.fn(), focus: vi.fn() };
  vi.stubGlobal("open", vi.fn().mockReturnValue(opened));
  let reads = 0;
  wire({
    [SETUP]: () => {
      reads += 1;
      return json(
        owed(
          reads === 1
            ? {}
            : {
                credentials: [
                  { label: "ufo GitHub App", filled: true, provider: "github", required: false },
                ],
              },
        ),
      );
    },
    [INTENTS]: () => json({ applied: true, message: "", url: "https://github.test/install" }),
  });
  mount();

  await userEvent.click(await stepTrigger("Install the workspace app"));
  await userEvent.click(await actButton("Install ufo GitHub App"));

  await waitFor(async () => {
    expect((await stepTrigger("Choose when it runs")).getAttribute("aria-expanded")).toBe("true");
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
  /** A feed trigger takes a registered source, shared, and a trigger naming it — more than one
   *  answer, so it is settled in chat. The row still has to carry the way there: stating the need
   *  and offering nothing left the member reading a chore with no way to do it. */
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
});

test("Build app stands below the todos and hands the ask over unsent", async () => {
  /** The todos are what the app needs before it can read anything; this is the act that turns a
   *  wired app into its screen, so it stands under them.
   *
   *  The ask is handed over rather than spent: it forks a site and binds a homepage, and a press
   *  that starts that before the member has read what it asks for is a surprise. It rides to the
   *  app's own new chat and stands in the composer, so the build runs in a conversation they
   *  opened, can watch, and can correct. */
  wire({ [SETUP]: () => json(owed()) });
  mount();

  await userEvent.click(await screen.findByRole("button", { name: "Build app" }));

  expect(location.hash).toBe("#/new/" + AGENT_ID);
  const handed = takePendingAsk(AGENT_ID, "new:" + AGENT_ID);
  expect(handed?.send).toBe(false);
  expect(handed?.text).toContain("Load your homepage skill");
  // The ask names the skill and stops: the steps live in the skill, and repeating them here would
  // be a second copy of the procedure that drifts the first time either changes.
  expect((handed?.text ?? "").length).toBeLessThan(160);
});

test("a member who is not an admin is told who installs it, and offered no press", async () => {
  wire({ [SETUP]: () => json(owed()) });
  mount(false);

  await userEvent.click(await stepTrigger("Install the workspace app"));
  expect(await screen.findByText("An admin installs this.")).toBeTruthy();
  expect(screen.queryAllByRole("button", { name: "Install" })).toHaveLength(0);
});

test("connect opens the consent window on the press, and points it at the turn's own link", async () => {
  /** The link is minted per speaking member at stream time — never in the intent's answer — so the
   *  turn's stream is where it arrives. The window is opened on the press because a window opened
   *  after the round trip has lost the gesture the browser opens one for. */
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
  /** The order already holds the prompt, and the kind holds content to the task's creator: a spec
   *  that set `prompt` again is refused for every admin who did not arm the order, and the cadence
   *  they pressed would stay unchanged. The re-pick sends the schedule alone. */
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
  // A schedule's first fire is its next cron occurrence: `next_fire` advances strictly past now and
  // the kind runs nothing one-shot. A label reading "Now, and every hour" promised a run that never
  // came, and left the member watching a silent app for an hour — for a week on a single weekday.
  expect(labelOf({ hour: null, minute: 0, weekdays: [] })).toBe("every hour");
  expect(labelOf({ hour: 9, minute: 0, weekdays: [1, 2, 3, 4, 5] })).toBe("every weekday at 9:00 AM");
  expect(labelOf({ hour: 9, minute: 0, weekdays: [] })).toBe("every day at 9:00 AM");
});

test("the cadence is stored in UTC, converted from the member's own clock", () => {
  // Greenwich: what the member picked is what the cron says.
  expect(cronFor({ hour: 9, minute: 0, weekdays: [] }, 0)).toBe("0 9 * * *");
  // Five hours west: 9am local is 14:00 UTC.
  expect(cronFor({ hour: 9, minute: 0, weekdays: [] }, 300)).toBe("0 14 * * *");
  // An hour that crosses midnight takes its weekdays with it: Monday 9pm local, five hours west, is
  // Tuesday 02:00 UTC — and a set that did not move would fire a day early every week.
  expect(cronFor({ hour: 21, minute: 0, weekdays: [1] }, 300)).toBe("0 2 * * 2");
  // An hourly cadence names no wall-clock time, so there is nothing to convert.
  expect(cronFor({ hour: null, minute: 0, weekdays: [] }, 300)).toBe("0 * * * *");
});

/** The shell reads the roster to know which app the workspace is still building, and a build lands
 *  long after that read. This screen holds the fresh answer, so it says when the page is there and
 *  the roster is read again. */
test("the screen says when the workspace has built the page, and stays quiet until it has", async () => {
  wire({ [SETUP]: () => json(owed()) });
  const built = vi.fn();
  mount(true, built);

  expect(await actButton("Connect Google Calendar")).toBeTruthy();
  expect(built).not.toHaveBeenCalled();
});

test("a page the workspace has built is stated once", async () => {
  wire({ [SETUP]: () => json(owed({ own_page: true })) });
  const built = vi.fn();
  mount(true, built);

  await waitFor(() => expect(built).toHaveBeenCalled());
});
