import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, onTestFinished, test, vi } from "vitest";

import { App } from "@/App";

import { AGENT, AGENT_ID, chatsOnWire, CONVO_ID, json, MEMBER, TURN_ID, type Route, useStreamFake, wire } from "./harness";
import { IMESSAGE_WATCH_MS } from "@/views/FirstRun";

const ADMIN = { ...MEMBER, admin: true };

const SLACK_LINK = "https://slack.com/oauth/v2/authorize?state=sealed";
const IMESSAGE_LINK = "sms:+14085550123?&body=UFO%20ABC123";

const OPENED = { turn_id: TURN_ID, conversation_id: CONVO_ID, title: "Setting up" };

/** The acts the run writes through, as their kinds project them: the member collection's add and
 *  the memory collection's write. */
const ADD_MEMBER = {
  name: "add_member",
  description: "Add a member to this workspace.",
  input_schema: {
    properties: {
      email: { type: "string", format: "email", title: "Email" },
      admin: { type: "boolean", title: "Admin", default: false },
      notify: { type: "boolean", title: "Notify", default: true },
    },
    required: ["email"],
  },
  call: { kind: "member", action: "add_member", input: {} },
  label: "Add member",
};

const RECORD_FIRST_RUN = {
  name: "record_first_run",
  description: "Record what the team uses.",
  input_schema: {
    properties: { body: { type: "string", title: "Body", maxLength: 200 } },
    required: ["body"],
  },
  call: { kind: "memory", action: "record_first_run", input: {} },
  label: "Continue",
};

/** The installs as the objects they act on project them, read by the connect steps on the press. */
const INSTALL_VIEW = (kind: string, name: string, action: string) => ({
  name: action,
  description: "Install the workspace app.",
  input_schema: { properties: {} },
  call: { kind, action, name, input: {} },
  label: "Connect",
});

const INSTALL_READS: Record<string, Route> = {
  "/actions/surface/slack$": () =>
    json({ actions: [INSTALL_VIEW("surface", "slack", "slack_connect")] }),
  "/actions/credential/github-app-installation$": () =>
    json({
      actions: [INSTALL_VIEW("credential", "github-app-installation", "connect_github")],
    }),
  "/actions/surface/imessage$": () =>
    json({ actions: [INSTALL_VIEW("surface", "imessage", "imessage_connect")] }),
};

const FIRST_RUN = {
  imessage: true,
  providers: [
    { name: "slack", label: "Slack" },
    { name: "github", label: "GitHub" },
    { name: "gmail", label: "Gmail" },
    { name: "googlecalendar", label: "Google Calendar" },
    { name: "googledrive", label: "Google Drive" },
    { name: "notion", label: "Notion" },
    { name: "linear", label: "Linear" },
  ],
  connectors: [
    { name: "slack", label: "Slack", installed: false },
    { name: "github", label: "GitHub", installed: false },
  ],
  actions: { member: [ADD_MEMBER], memory: [RECORD_FIRST_RUN] },
};

const HELD_SLACK = {
  ...FIRST_RUN,
  connectors: [
    { name: "slack", label: "Slack", installed: true },
    { name: "github", label: "GitHub", installed: false },
  ],
};

beforeEach(() => {
  location.hash = "";
  history.replaceState(null, "", location.pathname + "?first=1");
  useStreamFake();
});

/** The first run's own reads, plus whatever the case wires over them. */
function open(routes: Record<string, Route> = {}, member = ADMIN, payload = FIRST_RUN) {
  const wired = wire({
    ...chatsOnWire([]),
    ...INSTALL_READS,
    "/workspace/first-run": () => json(payload),
    ...routes,
  });
  render(<App agents={[AGENT]} member={member} onAgents={() => {}} />);
  return wired;
}

/** Every act the page submitted, in order: the lane below the agent it posted on — `intents` for
 *  an object mutation, the action route for a presented act — and the body it carried. */
function intents(calls: { url: string; body: unknown }[]): unknown[] {
  return calls.map((call) => ({ lane: laneOf(call.url), body: call.body }));
}

function laneOf(url: string): string {
  return url.split("/agents/" + AGENT.id + "/")[1];
}

/** The two lanes an act posts on, answering each with what that act's handler would — an action by
 *  the name its route ends in, any other verb by the verb. Anything unnamed applies, so a case
 *  states only the outcome it is about. */
function recorder(outcomes: Record<string, unknown> = {}): {
  calls: { url: string; body: unknown }[];
  route: Route;
} {
  const calls: { url: string; body: unknown }[] = [];
  return {
    calls,
    route: (url, init) => {
      const body = JSON.parse(String(init?.body));
      calls.push({ url, body });
      const lane = laneOf(url);
      const act = lane === "intents" ? body.verb : lane.split("/").at(-1);
      return json(outcomes[act] ?? { applied: true, message: "Saved." });
    },
  };
}

const lanes = (posted: ReturnType<typeof recorder>): Record<string, Route> => ({
  "/intents": posted.route,
  "/actions/": posted.route,
});

async function chooseGoal(goal = "Faster product dev", context = "") {
  await userEvent.click(await screen.findByRole("radio", { name: goal }));
  if (context) await userEvent.type(screen.getByLabelText("Add context"), context);
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
}

/** Records the picks and lands on the step that follows them. */
async function record(...picks: string[]) {
  await chooseGoal();
  for (const pick of picks) await userEvent.click(await screen.findByRole("button", { name: pick }));
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
}

/** The act that commits the step being asked for. */
function commit(): HTMLButtonElement {
  return screen.getByRole("button", { name: "Continue" }) as HTMLButtonElement;
}

/** The picks as the page writes them: one sentence naming the catalog's labels in the catalog's own
 *  order, posted as the memory collection's write. */
function toolingIntent(...providers: string[]) {
  const labels = FIRST_RUN.providers
    .filter((tile) => providers.includes(tile.name))
    .map((tile) => tile.label);
  return {
    lane: "actions/memory/record_first_run",
    body: { body: "My team uses " + labels.join(", ") + "." },
  };
}

const install = (kind: string, name: string, action: string) => ({
  lane: "actions/" + kind + "/" + name + "/" + action,
  body: {},
});

/** The member coming back from the provider's install page: the tab they left is looked at again,
 *  which is the whole account this page has of an install granted somewhere else. */
async function returning() {
  for (const state of ["hidden", "visible"]) {
    Object.defineProperty(document, "visibilityState", { value: state, configurable: true });
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
  }
}

test("the card's query lands on the first run's own address", async () => {
  open();

  await waitFor(() => expect(location.hash).toBe("#/first-run"));
  await screen.findByRole("heading", {
    name: "What do you want an agent to do for you today?",
  });
  expect(screen.queryByPlaceholderText("Start new chat…")).toBeNull();
});

test("the address opens the first run on its own, with no query at all", async () => {
  history.replaceState(null, "", location.pathname);
  location.hash = "#/first-run";
  open();

  await screen.findByRole("radio", { name: "Find PMF" });
  expect(location.hash).toBe("#/first-run");
});

test("the page draws no shell around the step", async () => {
  open();

  await screen.findByRole("heading", {
    name: "What do you want an agent to do for you today?",
  });
  expect(screen.queryByRole("banner")).toBeNull();
  expect(screen.queryByRole("button", { name: "Menu" })).toBeNull();
});

test("the head counts the three certain steps, and no connector leaves them three", async () => {
  const posted = recorder();
  open({ ...lanes(posted) });

  const progress = await screen.findByRole("progressbar", { name: "Step" });
  expect(progress.getAttribute("aria-valuenow")).toBe("1");
  expect(progress.getAttribute("aria-valuemax")).toBe("3");
  expect(progress.querySelectorAll('[aria-hidden="true"]')).toHaveLength(3);

  await chooseGoal();
  expect(progress.getAttribute("aria-valuenow")).toBe("2");
  expect(progress.getAttribute("aria-valuemax")).toBe("3");

  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  await screen.findByRole("heading", { name: "Invite your team" });
  expect(progress.getAttribute("aria-valuenow")).toBe("3");
  expect(progress.getAttribute("aria-valuemax")).toBe("3");
  expect(progress.querySelectorAll('[aria-hidden="true"]')).toHaveLength(3);
});

/** The head never states the run finished while a step it is certain to draw is still ahead. The
 *  team step always comes, so it is counted from the first screen rather than appearing once the
 *  tools are recorded. */
test("the run never counts itself complete before its last step", async () => {
  const posted = recorder();
  open({ ...lanes(posted) });

  const progress = await screen.findByRole("progressbar", { name: "Step" });
  await chooseGoal();

  expect(Number(progress.getAttribute("aria-valuenow"))).toBeLessThan(
    Number(progress.getAttribute("aria-valuemax")),
  );
});

test("both connectors reveal a five-step run", async () => {
  const posted = recorder();
  open({ ...lanes(posted) });

  await record("Slack", "GitHub");

  const progress = screen.getByRole("progressbar", { name: "Step" });
  expect(progress.getAttribute("aria-valuenow")).toBe("3");
  expect(progress.getAttribute("aria-valuemax")).toBe("5");
  expect(progress.querySelectorAll('[aria-hidden="true"]')).toHaveLength(5);
});

test("the goal is the whole first step", async () => {
  open();

  const choices = await screen.findAllByRole("radio");
  expect(choices.map((choice) => choice.textContent)).toEqual([
    "Faster product dev",
    "More revenue",
    "Automate ops",
    "Find PMF",
  ]);
  expect(choices.every((choice) => choice.getAttribute("aria-checked") === "false")).toBe(true);
  expect(screen.getByLabelText("Add context")).toBeTruthy();
  expect(commit().disabled).toBe(true);
  expect(screen.queryByRole("button", { name: "Slack" })).toBeNull();
  expect(screen.queryByRole("heading", { name: "Add @ufo to Slack" })).toBeNull();
  expect(screen.queryByRole("heading", { name: "Invite your team" })).toBeNull();
});

/** One goal is held at a time, and picking another releases the one before it. The assertion reads
 *  the spoken state rather than the check drawn beside it: every row carries the glyph and the pick
 *  only reveals it, so what a member sees is a paint this environment computes no styles for. */
test("the goal picked releases the one before it", async () => {
  open();

  const choices = await screen.findAllByRole("radio");
  expect(choices.map((choice) => choice.getAttribute("aria-checked"))).toEqual([
    "false",
    "false",
    "false",
    "false",
  ]);

  await userEvent.click(choices[0]);
  expect(choices.map((choice) => choice.getAttribute("aria-checked"))).toEqual([
    "true",
    "false",
    "false",
    "false",
  ]);

  await userEvent.click(choices[2]);
  expect(choices.map((choice) => choice.getAttribute("aria-checked"))).toEqual([
    "false",
    "false",
    "true",
    "false",
  ]);
});

test("free text can state another goal", async () => {
  open();

  await userEvent.type(await screen.findByLabelText("Add context"), "Reduce support response time");
  expect(commit().disabled).toBe(false);
  await userEvent.click(commit());

  await screen.findByRole("heading", { name: "What your team uses" });
});

test("the tools step names the agent, and the mention carries the strong weight", async () => {
  open();

  await userEvent.click((await screen.findAllByRole("radio"))[0]);
  await userEvent.click(commit());

  const tools = (await screen.findByRole("heading", { name: "What your team uses" })).parentElement!;
  expect(tools.querySelector("p")!.textContent).toBe(
    "Pick the ones your team works in, so @ufo knows where your work lives.",
  );
  expect([...tools.querySelectorAll("strong.font-strong")].map((at) => at.textContent)).toEqual([
    "@ufo",
  ]);
});

test("the picks are recorded through the intent lane, and only what was picked is asked for", async () => {
  const posted = recorder();
  open({ ...lanes(posted) });

  await record("Notion", "Slack");

  expect(intents(posted.calls)).toEqual([toolingIntent("notion", "slack")]);
  await screen.findByRole("heading", { name: "Add @ufo to Slack" });
  expect(screen.getByRole("button", { name: "Connect Slack" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Connect GitHub" })).toBeNull();
  // One step stands at a time: the answered step leaves the page rather than sitting above the
  // one being asked for.
  expect(screen.queryByRole("button", { name: "Notion" })).toBeNull();
  expect(screen.queryByRole("heading", { name: "What your team uses" })).toBeNull();
});

test("picking neither connector skips to the invite step", async () => {
  const posted = recorder();
  open({ ...lanes(posted) });

  await record("Notion");

  const invite = (await screen.findByRole("heading", { name: "Invite your team" })).parentElement!;
  expect(invite.querySelector("p")!.textContent).toBe(
    "ufo.ai is best with a team, and we don't charge per seat.",
  );
  expect(screen.queryByRole("heading", { name: "Add @ufo to Slack" })).toBeNull();
});

test("each connector picked is its own step, in the order the tiles offer them", async () => {
  const posted = recorder();
  open({ ...lanes(posted) });

  await record("GitHub", "Slack");

  // Slack leads because the catalog does, not because it was picked second.
  await screen.findByRole("heading", { name: "Add @ufo to Slack" });
  expect(screen.getByRole("button", { name: "Connect Slack" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Connect GitHub" })).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Skip" }));
  await screen.findByRole("heading", { name: "Add the ufo-ai bot to GitHub" });
  expect(screen.getByRole("button", { name: "Connect GitHub" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Connect Slack" })).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Skip" }));
  await screen.findByRole("heading", { name: "Invite your team" });
  expect(screen.getByRole("button", { name: "Invite" })).toBeTruthy();
});

/** Both connect steps name the agent the member is about to let in, and every mention of it stands
 *  in the heavier weight the theme keeps for emphasis. */
test("each connect step names the agent, and the mentions carry the strong weight", async () => {
  const posted = recorder();
  open({ ...lanes(posted) });

  await record("GitHub", "Slack");

  const slack = (await screen.findByRole("heading", { name: "Add @ufo to Slack" }))
    .parentElement!;
  expect(slack.querySelector("p")!.textContent).toBe(
    "Mention @ufo in a channel or send a direct message. @ufo replies, remembers, and works with your team.",
  );
  expect([...slack.querySelectorAll("strong.font-strong")].map((at) => at.textContent)).toEqual([
    "@ufo",
    "@ufo",
    "@ufo",
  ]);

  await userEvent.click(screen.getByRole("button", { name: "Skip" }));

  const github = (
    await screen.findByRole("heading", { name: "Add the ufo-ai bot to GitHub" })
  ).parentElement!;
  expect(github.querySelector("p")!.textContent).toBe(
    "ufo-ai reads code, reviews pull requests, and pushes changes. You pick which repositories on GitHub.",
  );
  expect([...github.querySelectorAll("strong.font-strong")].map((at) => at.textContent)).toEqual([
    "ufo-ai",
    "ufo-ai",
  ]);
});

test("the foot carries only the acts the step has", async () => {
  const posted = recorder();
  open({ ...lanes(posted) });

  await screen.findByRole("radio", { name: "Faster product dev" });
  expect(screen.queryByRole("button", { name: "Back" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Skip" })).toBeNull();
  expect(commit().disabled).toBe(true);

  await record("Slack");

  await screen.findByRole("heading", { name: "Add @ufo to Slack" });
  expect(screen.getByRole("button", { name: "Back" })).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Skip" }));

  await screen.findByRole("heading", { name: "Invite your team" });
  expect(screen.getByRole("button", { name: "Back" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Skip" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Continue" })).toBeNull();
  // Nothing is written yet, so there is nobody to invite — Skip is the way past.
  expect((screen.getByRole("button", { name: "Invite" }) as HTMLButtonElement).disabled).toBe(true);
});

test("continue is held closed until the install itself lands, not until the link is minted", async () => {
  const posted = recorder({ slack_connect: { applied: true, message: "", url: SLACK_LINK } });
  open({ ...lanes(posted) });

  await record("Slack", "GitHub");

  await screen.findByRole("heading", { name: "Add @ufo to Slack" });
  expect(commit().disabled).toBe(true);
  await userEvent.click(screen.getByRole("button", { name: "Connect Slack" }));

  // The member has been handed the install page and has not been to it: the workspace holds
  // nothing yet, and the step says so.
  await screen.findByRole("link", { name: "Open the Slack install page" });
  await returning();
  expect(commit().disabled).toBe(true);
  expect(screen.getByRole("heading", { name: "Add @ufo to Slack" })).toBeTruthy();
});

test("one press opens the consent window and lands the minted link in it", async () => {
  const posted = recorder({ slack_connect: { applied: true, message: "", url: SLACK_LINK } });
  open({ ...lanes(posted) });
  const consent = { focus: vi.fn(), close: vi.fn(), location: { href: "" } };
  const opened = vi.spyOn(window, "open").mockReturnValue(consent as unknown as Window);

  await record("Slack", "GitHub");
  await screen.findByRole("heading", { name: "Add @ufo to Slack" });
  await userEvent.click(screen.getByRole("button", { name: "Connect Slack" }));

  // The window opens on the press, before the link exists — a window opened after the round trip
  // has lost the gesture the browser opens one for.
  const [url, name, features] = opened.mock.calls[0];
  expect(url).toBe("");
  expect(name).toBe("ufo-connect");
  expect(features).toContain("popup");
  // Then the minted link lands in it, so there is nothing under the button to press next.
  await waitFor(() => expect(consent.location.href).toBe(SLACK_LINK));
  expect(screen.queryByRole("link", { name: "Open the Slack install page" })).toBeNull();
  opened.mockRestore();
});

test("a browser that refuses the window still hands the member the link", async () => {
  const posted = recorder({ slack_connect: { applied: true, message: "", url: SLACK_LINK } });
  open({ ...lanes(posted) });
  const opened = vi.spyOn(window, "open").mockReturnValue(null);

  await record("Slack", "GitHub");
  await screen.findByRole("heading", { name: "Add @ufo to Slack" });
  await userEvent.click(screen.getByRole("button", { name: "Connect Slack" }));

  const link = await screen.findByRole("link", { name: "Open the Slack install page" });
  expect(link.getAttribute("href")).toBe(SLACK_LINK);
  opened.mockRestore();
});

test("a refused connect closes the window it opened rather than parking it on nothing", async () => {
  const posted = recorder({
    connect_slack: { applied: false, message: "A workspace admin connects Slack." },
  });
  open({ ...lanes(posted) });
  const consent = { focus: vi.fn(), close: vi.fn(), location: { href: "" } };
  const opened = vi.spyOn(window, "open").mockReturnValue(consent as unknown as Window);

  await record("Slack", "GitHub");
  await screen.findByRole("heading", { name: "Add @ufo to Slack" });
  await userEvent.click(screen.getByRole("button", { name: "Connect Slack" }));

  await waitFor(() => expect(consent.close).toHaveBeenCalled());
  expect(consent.location.href).toBe("");
  opened.mockRestore();
});

test("the connect step passes itself when the install lands", async () => {
  const posted = recorder({ slack_connect: { applied: true, message: "", url: SLACK_LINK } });
  let landed = false;
  open({
    ...lanes(posted),
    "/workspace/first-run": () => json(landed ? HELD_SLACK : FIRST_RUN),
  });

  await record("Slack", "GitHub");
  await screen.findByRole("heading", { name: "Add @ufo to Slack" });
  await userEvent.click(screen.getByRole("button", { name: "Connect Slack" }));
  await screen.findByRole("link", { name: "Open the Slack install page" });

  landed = true;
  await returning();

  // Nothing was pressed: the member finished on Slack's pages and comes back to the next question.
  await screen.findByRole("heading", { name: "Add the ufo-ai bot to GitHub" });
  expect(commit().disabled).toBe(true);
});

test("a connector the workspace already held waits to be read rather than passing itself", async () => {
  const posted = recorder();
  open({ ...lanes(posted) }, ADMIN, HELD_SLACK);

  await record("Slack");
  await screen.findByText("Slack connected");
  await returning();

  expect(screen.getByRole("heading", { name: "Add @ufo to Slack" })).toBeTruthy();
  expect(commit().disabled).toBe(false);
});

test("a step passed by its own install is connected on the way back to it", async () => {
  const posted = recorder({ slack_connect: { applied: true, message: "", url: SLACK_LINK } });
  let landed = false;
  open({
    ...lanes(posted),
    "/workspace/first-run": () => json(landed ? HELD_SLACK : FIRST_RUN),
  });

  await record("Slack", "GitHub");
  await userEvent.click(await screen.findByRole("button", { name: "Connect Slack" }));
  await screen.findByRole("link", { name: "Open the Slack install page" });
  landed = true;
  await returning();
  await screen.findByRole("heading", { name: "Add the ufo-ai bot to GitHub" });
  await userEvent.click(screen.getByRole("button", { name: "Back" }));

  // The step states what the workspace holds and stands still: passing it again would take the
  // member the one way they did not ask to go.
  await screen.findByText("Slack connected");
  expect(commit().disabled).toBe(false);

});

test("a step passed while the tab stayed open is settled before the page's own read lands", async () => {
  vi.useFakeTimers();
  onTestFinished(() => {
    vi.useRealTimers();
  });
  const posted = recorder({ slack_connect: { applied: true, message: "", url: SLACK_LINK } });
  let landed = false;
  open({
    ...lanes(posted),
    "/workspace/first-run": () => json(landed ? HELD_SLACK : FIRST_RUN),
  });
  const settle = () => act(async () => void (await vi.advanceTimersByTimeAsync(0)));

  await settle();
  fireEvent.click(screen.getByRole("radio", { name: "Faster product dev" }));
  fireEvent.click(commit());
  await settle();
  fireEvent.click(screen.getByRole("button", { name: "Slack" }));
  fireEvent.click(screen.getByRole("button", { name: "GitHub" }));
  fireEvent.click(commit());
  await settle();
  fireEvent.click(screen.getByRole("button", { name: "Connect Slack" }));
  await settle();

  // The install is granted in another window and this tab never goes away, so the step's own watch
  // is the only read that sees it: the page's own read is half a minute behind.
  landed = true;
  await act(async () => void (await vi.advanceTimersByTimeAsync(3_000)));
  expect(screen.getByRole("heading", { name: "Add the ufo-ai bot to GitHub" })).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "Back" }));
  await settle();

  expect(screen.getByText("Slack connected")).toBeTruthy();
  expect(commit().disabled).toBe(false);
});

test("skip leaves the connect step behind with nothing connected", async () => {
  const posted = recorder();
  open({ ...lanes(posted) });

  await record("Slack");
  await userEvent.click(await screen.findByRole("button", { name: "Skip" }));

  await screen.findByRole("heading", { name: "Invite your team" });
  expect(intents(posted.calls)).toEqual([toolingIntent("slack")]);
});

test("back re-opens the tiles with the picks still on them and still editable", async () => {
  const posted = recorder();
  open({ ...lanes(posted) });

  await record("Slack", "GitHub");
  await userEvent.click(await screen.findByRole("button", { name: "Back" }));

  const slack = await screen.findByRole("button", { name: "Slack" });
  expect(slack.getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByRole("button", { name: "GitHub" }).getAttribute("aria-pressed")).toBe("true");
  expect((slack as HTMLButtonElement).disabled).toBe(false);
  await userEvent.click(slack);
  expect(slack.getAttribute("aria-pressed")).toBe("false");
});

test("a changed pick on the way back is recorded again", async () => {
  const posted = recorder();
  open({ ...lanes(posted) });

  await record("Slack");
  await userEvent.click(await screen.findByRole("button", { name: "Back" }));
  await userEvent.click(await screen.findByRole("button", { name: "GitHub" }));
  await userEvent.click(commit());

  await screen.findByRole("button", { name: "Connect Slack" });
  expect(intents(posted.calls)).toEqual([toolingIntent("slack"), toolingIntent("slack", "github")]);
});

test("picks left as they were on the way back are not recorded twice", async () => {
  const posted = recorder();
  open({ ...lanes(posted) });

  await record("Slack");
  await userEvent.click(await screen.findByRole("button", { name: "Back" }));
  await userEvent.click(commit());

  await screen.findByRole("button", { name: "Connect Slack" });
  expect(intents(posted.calls)).toEqual([toolingIntent("slack")]);
});

test("a connector the workspace already holds states so and carries no act", async () => {
  const posted = recorder();
  open({ ...lanes(posted) }, ADMIN, HELD_SLACK);

  await record("Slack");

  await screen.findByText("Slack connected");
  expect(screen.queryByRole("button", { name: "Connect Slack" })).toBeNull();
  expect(commit().disabled).toBe(false);
});

test("connecting mints the link on the intent lane, never through the chat", async () => {
  const posted = recorder({ slack_connect: { applied: true, message: "", url: SLACK_LINK } });
  open({ ...lanes(posted) });

  await record("Slack");
  await userEvent.click(await screen.findByRole("button", { name: "Connect Slack" }));

  const link = await screen.findByRole("link", { name: "Open the Slack install page" });
  expect(link.getAttribute("href")).toBe(SLACK_LINK);
  expect(intents(posted.calls).at(-1)).toEqual(install("surface", "slack", "slack_connect"));
  expect(location.hash).toBe("#/first-run");
  expect(screen.queryByPlaceholderText("Start new chat…")).toBeNull();
});

test("a link minted on one connector's step is not shown on the next one's", async () => {
  const posted = recorder({ slack_connect: { applied: true, message: "", url: SLACK_LINK } });
  open({ ...lanes(posted) });

  await record("Slack", "GitHub");
  await userEvent.click(await screen.findByRole("button", { name: "Connect Slack" }));
  await screen.findByRole("link", { name: "Open the Slack install page" });
  await userEvent.click(screen.getByRole("button", { name: "Skip" }));

  await screen.findByRole("button", { name: "Connect GitHub" });
  expect(screen.queryByRole("link")).toBeNull();
});

test("the GitHub row submits the installation credential's own connect action", async () => {
  const posted = recorder({
    connect_github: { applied: true, message: "", url: "https://github.com/apps/ufo-ai" },
  });
  open({ ...lanes(posted) });

  await record("GitHub");
  await userEvent.click(await screen.findByRole("button", { name: "Connect GitHub" }));

  await screen.findByRole("link", { name: "Open the GitHub install page" });
  expect(intents(posted.calls).at(-1)).toEqual(
    install("credential", "github-app-installation", "connect_github"),
  );
});

test("a connector whose object projects no act states the refusal and posts nothing", async () => {
  const posted = recorder();
  open({ ...lanes(posted), "/actions/surface/slack$": () => json({ actions: [] }) });

  await record("Slack");
  await userEvent.click(await screen.findByRole("button", { name: "Connect Slack" }));

  await screen.findByText("This act is not available here.");
  expect(intents(posted.calls).filter((act) => (act as { lane: string }).lane.startsWith("actions/surface/"))).toEqual([]);
});

test("a connect the tool minted no link for states what it answered instead", async () => {
  const posted = recorder({
    slack_connect: {
      applied: true,
      message: "Ask a workspace admin to connect Slack — only they can install it.",
      url: null,
    },
  });
  open({ ...lanes(posted) });

  await record("Slack");
  await userEvent.click(await screen.findByRole("button", { name: "Connect Slack" }));

  await screen.findByText("Ask a workspace admin to connect Slack — only they can install it.");
  expect(screen.queryByRole("link")).toBeNull();
  expect(commit().disabled).toBe(true);
});

test("a member who is not an admin is told who installs, and presses nothing", async () => {
  const posted = recorder();
  open({ ...lanes(posted) }, MEMBER);

  await record("Slack");

  await screen.findByText("A workspace admin connects Slack.");
  expect(screen.queryByRole("button", { name: "Connect Slack" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Skip" }));
  await screen.findByText("A workspace admin adds members.");
  expect(screen.queryByLabelText("Email 1")).toBeNull();
  expect(intents(posted.calls)).toEqual([toolingIntent("slack")]);
});

test("the invite step opens on three boxes and grows one at a time", async () => {
  const posted = recorder();
  open({ ...lanes(posted) });

  await record("Notion");

  await screen.findByLabelText("Email 1");
  expect(screen.getByLabelText("Email 2")).toBeTruthy();
  expect(screen.getByLabelText("Email 3")).toBeTruthy();
  expect(screen.queryByLabelText("Email 4")).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Add another" }));
  expect(screen.getByLabelText("Email 4")).toBeTruthy();
});

test("the first box takes the cursor as the step arrives", async () => {
  const posted = recorder();
  open({ ...lanes(posted) });

  await record("Notion");

  const first = await screen.findByLabelText("Email 1");
  await waitFor(() => expect(document.activeElement).toBe(first));
});

test("every address written is added on one press, and then iMessage is offered", async () => {
  const sent: string[] = [];
  const posted = recorder();
  open({
    ...lanes(posted),
    "/chat": (_url, init) => {
      sent.push(String(init?.body));
      return json(OPENED);
    },
  });

  await record("Notion");
  await userEvent.type(await screen.findByLabelText("Email 1"), "sam@work.com");
  await userEvent.type(screen.getByLabelText("Email 3"), "alex@work.com");
  await userEvent.click(screen.getByRole("button", { name: "Invite" }));

  // A blank box is not an address: only what was written is asked for, in the order it stands in.
  await waitFor(() =>
    expect(intents(posted.calls).slice(1)).toEqual([
      { lane: "actions/member/add_member", body: { email: "sam@work.com", admin: false } },
      { lane: "actions/member/add_member", body: { email: "alex@work.com", admin: false } },
    ]),
  );
  await screen.findByRole("heading", { name: "Use this agent in iMessage" });
  expect(sent).toEqual([]);
});

test("skipping every invite offers iMessage before the chat opens", async () => {
  const sent: string[] = [];
  const posted = recorder();
  open({
    ...lanes(posted),
    "/chat": (_url, init) => {
      sent.push(String(init?.body));
      return json(OPENED);
    },
  });

  await record("Notion");
  await userEvent.click(await screen.findByRole("button", { name: "Skip" }));

  await screen.findByRole("heading", { name: "Use this agent in iMessage" });
  expect(screen.getByText("Connect your phone to message UFO anytime.")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Connect iMessage" })).toBeTruthy();
  expect(sent).toEqual([]);
});

test("a deploy without iMessage finishes when every invite is skipped", async () => {
  const sent: string[] = [];
  const posted = recorder();
  open(
    {
      ...lanes(posted),
      "/chat": (_url, init) => {
        sent.push(String(init?.body));
        return json(OPENED);
      },
    },
    ADMIN,
    { ...FIRST_RUN, imessage: false },
  );

  await record("Notion");
  await userEvent.click(await screen.findByRole("button", { name: "Skip" }));

  await waitFor(() =>
    expect(sent).toEqual([
      "I just set up this workspace. I want to develop products faster, and we use Notion.",
    ]),
  );
  expect(screen.queryByRole("heading", { name: "Use this agent in iMessage" })).toBeNull();
});

test("the iMessage offer starts the phone claim through the intent lane", async () => {
  const sent: string[] = [];
  const posted = recorder({
    imessage_connect: {
      applied: true,
      message: 'Text "UFO ABC123" to (408) 555-0123 from that phone within 30 minutes.',
      url: IMESSAGE_LINK,
    },
  });
  const { calls } = open({
    ...lanes(posted),
    "/chat": (_url, init) => {
      sent.push(String(init?.body));
      return json(OPENED);
    },
  });

  await record("Notion");
  await userEvent.click(await screen.findByRole("button", { name: "Skip" }));
  const connect = await screen.findByRole("button", { name: "Connect iMessage" });
  expect((connect as HTMLButtonElement).disabled).toBe(true);
  await userEvent.type(screen.getByLabelText("iMessage phone number"), "(559).425/99-91");
  expect((screen.getByLabelText("iMessage phone number") as HTMLInputElement).value).toBe(
    "(559) 425-9991",
  );
  fireEvent.change(screen.getByLabelText("iMessage phone number"), {
    target: { value: "+1 (559) 425-9991" },
  });
  expect((screen.getByLabelText("iMessage phone number") as HTMLInputElement).value).toBe(
    "(559) 425-9991",
  );
  await userEvent.click(await screen.findByRole("button", { name: "Connect iMessage" }));

  expect(
    (
      await screen.findByRole("link", {
        name: 'Text "UFO ABC123" to (408) 555-0123',
      })
    ).getAttribute("href"),
  ).toBe(IMESSAGE_LINK);
  expect(screen.getByRole("link", { name: "Text code to UFO" }).getAttribute("href")).toBe(
    IMESSAGE_LINK,
  );
  const textCode = screen.getByRole("link", { name: "Text code to UFO" });
  const openSpy = vi.spyOn(window, "open");
  textCode.addEventListener("click", (event) => event.preventDefault(), { once: true });
  fireEvent.click(textCode);
  expect(openSpy).not.toHaveBeenCalled();
  expect(textCode.getAttribute("target")).toBeNull();
  expect(intents(posted.calls).at(-1)).toEqual({
    lane: "actions/surface/imessage/imessage_connect",
    body: { phone_number: "+15594259991" },
  });
  // The act is the one the surface row's declaration projects, read before the press posts it.
  const projected = calls.indexOf("/surface/web/actions/surface/imessage");
  const posted_at = calls.findIndex((url) => url.endsWith("/actions/surface/imessage/imessage_connect"));
  expect(projected).toBeGreaterThanOrEqual(0);
  expect(projected).toBeLessThan(posted_at);
  await userEvent.click(screen.getByRole("button", { name: "Nevermind" }));
  await waitFor(() =>
    expect(sent).toEqual([
      "I just set up this workspace. I want to develop products faster, and we use Notion.",
    ]),
  );
});

test("the pending iMessage step confirms once the phone proves its code", async () => {
  const sent: string[] = [];
  let claim = { state: "pending" };
  const posted = recorder({
    imessage_connect: {
      applied: true,
      message: 'Text "UFO ABC123" to (408) 555-0123 from that phone within 30 minutes.',
      url: IMESSAGE_LINK,
    },
  });
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  try {
    open({
      ...lanes(posted),
      "/workspace/imessage-claim": () => json(claim),
      "/chat": (_url, init) => {
        sent.push(String(init?.body));
        return json(OPENED);
      },
    });

    await record("Notion");
    await userEvent.click(await screen.findByRole("button", { name: "Skip" }));
    await userEvent.type(screen.getByLabelText("iMessage phone number"), "(559) 425-9991");
    await userEvent.click(await screen.findByRole("button", { name: "Connect iMessage" }));

    await screen.findByRole("link", { name: "Text code to UFO" });
    expect(
      screen.queryByText("Phone connected — you can now message UFO from iMessage."),
    ).toBeNull();

    claim = { state: "connected" };
    for (const state of ["hidden", "visible"]) {
      Object.defineProperty(document, "visibilityState", { value: state, configurable: true });
      await act(async () => {
        document.dispatchEvent(new Event("visibilitychange"));
      });
    }
    await act(async () => {
      await vi.advanceTimersByTimeAsync(IMESSAGE_WATCH_MS);
      await Promise.resolve();
      await Promise.resolve();
    });
    await screen.findByText("Phone connected — you can now message UFO from iMessage.");
    expect(screen.queryByRole("link", { name: "Text code to UFO" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() =>
      expect(sent).toEqual([
        "I just set up this workspace. I want to develop products faster, and we use Notion.",
      ]),
    );
  } finally {
    vi.useRealTimers();
  }
});

test("the pending iMessage step states the lapsed window when the claim expires", async () => {
  const posted = recorder({
    imessage_connect: {
      applied: true,
      message: 'Text "UFO ABC123" to (408) 555-0123 from that phone within 30 minutes.',
      url: IMESSAGE_LINK,
    },
  });
  open({
    ...lanes(posted),
    "/workspace/imessage-claim": () => json({ state: "expired" }),
  });

  await record("Notion");
  await userEvent.click(await screen.findByRole("button", { name: "Skip" }));
  await userEvent.type(screen.getByLabelText("iMessage phone number"), "(559) 425-9991");
  await userEvent.click(await screen.findByRole("button", { name: "Connect iMessage" }));

  await screen.findByText("That code expired. Connect iMessage again for a new one.");
  expect(screen.queryByRole("link", { name: "Text code to UFO" })).toBeNull();
});

test("the lapsed iMessage step mints another code from the same page", async () => {
  let claim = { state: "expired" };
  const posted = recorder({
    imessage_connect: {
      applied: true,
      message: 'Text "UFO ABC123" to (408) 555-0123 from that phone within 30 minutes.',
      url: IMESSAGE_LINK,
    },
  });
  open({
    ...lanes(posted),
    "/workspace/imessage-claim": () => json(claim),
  });

  await record("Notion");
  await userEvent.click(await screen.findByRole("button", { name: "Skip" }));
  await userEvent.type(screen.getByLabelText("iMessage phone number"), "(559) 425-9991");
  await userEvent.click(await screen.findByRole("button", { name: "Connect iMessage" }));

  await screen.findByText("That code expired. Connect iMessage again for a new one.");
  claim = { state: "pending" };
  await userEvent.click(screen.getByRole("button", { name: "Connect iMessage again" }));

  const field = (await screen.findByLabelText("iMessage phone number")) as HTMLInputElement;
  expect(field.value).toBe("(559) 425-9991");
  await userEvent.click(screen.getByRole("button", { name: "Connect iMessage" }));

  await screen.findByRole("link", { name: "Text code to UFO" });
  expect(screen.queryByText("That code expired. Connect iMessage again for a new one.")).toBeNull();
  expect(intents(posted.calls)).toEqual([
    toolingIntent("notion"),
    { lane: "actions/surface/imessage/imessage_connect", body: { phone_number: "+15594259991" } },
    { lane: "actions/surface/imessage/imessage_connect", body: { phone_number: "+15594259991" } },
  ]);
});

test("an invalid iMessage phone stays on the form with one instruction", async () => {
  const posted = recorder();
  open({ ...lanes(posted) });

  await record("Notion");
  await userEvent.click(await screen.findByRole("button", { name: "Skip" }));
  await userEvent.type(screen.getByLabelText("iMessage phone number"), "425-9991");
  await userEvent.click(screen.getByRole("button", { name: "Connect iMessage" }));

  expect(
    screen.getByText("Enter a 10-digit US phone number."),
  ).toBeTruthy();
  await userEvent.clear(screen.getByLabelText("iMessage phone number"));
  await userEvent.type(screen.getByLabelText("iMessage phone number"), "000-000-0000");
  await userEvent.click(screen.getByRole("button", { name: "Connect iMessage" }));
  expect(screen.getByText("Enter a 10-digit US phone number.")).toBeTruthy();
  expect(intents(posted.calls)).toEqual([toolingIntent("notion")]);
});

test("an iMessage phone that states another country code is refused, not cut to ten digits", async () => {
  const posted = recorder();
  open({ ...lanes(posted) });

  await record("Notion");
  await userEvent.click(await screen.findByRole("button", { name: "Skip" }));
  fireEvent.change(await screen.findByLabelText("iMessage phone number"), {
    target: { value: "+44 7911 123456" },
  });
  expect((screen.getByLabelText("iMessage phone number") as HTMLInputElement).value).toBe(
    "+44 7911 123456",
  );
  fireEvent.change(screen.getByLabelText("iMessage phone number"), {
    target: { value: "(+45) 12 34 56 78" },
  });
  expect((screen.getByLabelText("iMessage phone number") as HTMLInputElement).value).toBe(
    "(+45) 12 34 56 78",
  );
  await userEvent.click(screen.getByRole("button", { name: "Connect iMessage" }));

  expect(screen.getByText("Enter a 10-digit US phone number.")).toBeTruthy();
  expect(intents(posted.calls)).toEqual([toolingIntent("notion")]);
});

test("back from the iMessage offer returns to the invite fields", async () => {
  const posted = recorder();
  open({ ...lanes(posted) });

  await record("Notion");
  await userEvent.type(await screen.findByLabelText("Email 1"), "sam@work.com");
  await userEvent.click(screen.getByRole("button", { name: "Skip" }));
  await userEvent.click(await screen.findByRole("button", { name: "Back" }));

  expect(await screen.findByRole("heading", { name: "Invite your team" })).toBeTruthy();
  expect((screen.getByLabelText("Email 1") as HTMLInputElement).value).toBe("sam@work.com");
});

test("a refused invite states the refusal and adds nobody", async () => {
  const posted = recorder({
    add_member: { applied: false, message: "only a workspace admin can add members" },
  });
  open({ ...lanes(posted) });

  await record("Notion");
  await userEvent.type(await screen.findByLabelText("Email 1"), "teammate@work.com");
  await userEvent.click(screen.getByRole("button", { name: "Invite" }));

  await screen.findByText("only a workspace admin can add members");
  expect((screen.getByLabelText("Email 1") as HTMLInputElement).value).toBe("teammate@work.com");
  // The refusal stops the run where it happened rather than handing the member on.
  expect(screen.queryByPlaceholderText("Start new chat…")).toBeNull();
});

test("the last act says the picks and the question into the agent's new chat", async () => {
  const sent: { url: string; body: string }[] = [];
  const posted = recorder();
  open({
    ...lanes(posted),
    "/chat": (url, init) => {
      sent.push({ url, body: String(init?.body) });
      return json(OPENED);
    },
  });

  // Picked out of catalog order: the message states them in the order the tiles are offered, so
  // one pick set produces one message however the member clicked it.
  await chooseGoal("Find PMF", "Help us recruit the right interviewees");
  await userEvent.click(await screen.findByRole("button", { name: "Notion" }));
  await userEvent.click(screen.getByRole("button", { name: "Gmail" }));
  await userEvent.click(commit());
  await userEvent.click(await screen.findByRole("button", { name: "Skip" }));
  await screen.findByRole("heading", { name: "Use this agent in iMessage" });
  await userEvent.click(screen.getByRole("button", { name: "Skip" }));

  await waitFor(() => expect(sent.length).toBe(1));
  expect(sent[0].url).toBe("/surface/web/agents/" + AGENT_ID + "/chat?conversation=new");
  expect(sent[0].body).toBe(
    "I just set up this workspace. " +
      "I want to find product-market fit, and we use Gmail, Notion. " +
      "More context: Help us recruit the right interviewees.",
  );
  const box = await screen.findByLabelText("Ask UFO");
  expect((box as HTMLTextAreaElement).value).toBe("");
  // The page creates no agent: creating one takes a speaking member, and the page never speaks.
  expect(intents(posted.calls)).toEqual([toolingIntent("notion", "gmail")]);
});

test("picking no connector records nothing and still sends the goal", async () => {
  const sent: string[] = [];
  const posted = recorder();
  open({
    ...lanes(posted),
    "/chat": (_url, init) => {
      sent.push(String(init?.body));
      return json(OPENED);
    },
  });

  await chooseGoal("More revenue");
  await userEvent.click(await screen.findByRole("button", { name: "Continue" }));
  await screen.findByRole("heading", { name: "Invite your team" });
  await userEvent.click(screen.getByRole("button", { name: "Skip" }));
  await screen.findByRole("heading", { name: "Use this agent in iMessage" });
  await userEvent.click(screen.getByRole("button", { name: "Skip" }));

  await waitFor(() =>
    expect(sent).toEqual(["I just set up this workspace. I want to increase revenue."]),
  );
  expect(intents(posted.calls)).toEqual([]);
});

test("free text becomes the initial prompt", async () => {
  const sent: string[] = [];
  const posted = recorder();
  open({
    ...lanes(posted),
    "/chat": (_url, init) => {
      sent.push(String(init?.body));
      return json(OPENED);
    },
  });

  const context = await screen.findByLabelText("Add context");
  expect(context).toBeInstanceOf(HTMLInputElement);
  expect(context.getAttribute("placeholder")).toBe("More information");
  await userEvent.type(context, "Reduce support response time.");
  await userEvent.click(commit());
  await userEvent.click(await screen.findByRole("button", { name: "Continue" }));
  await screen.findByRole("heading", { name: "Invite your team" });
  await userEvent.click(screen.getByRole("button", { name: "Skip" }));
  await screen.findByRole("heading", { name: "Use this agent in iMessage" });
  await userEvent.click(screen.getByRole("button", { name: "Skip" }));

  await waitFor(() =>
    expect(sent).toEqual([
      "I just set up this workspace. " +
        "I want an agent to help with this goal: Reduce support response time.",
    ]),
  );
  expect(intents(posted.calls)).toEqual([]);
});

test("a refused record keeps the member on the first step and states the refusal", async () => {
  open({ "/actions/": () => json({ applied: false, message: "No memory extension." }) });

  await record("Gmail");

  await screen.findByText("No memory extension.");
  expect(location.hash).toBe("#/first-run");
  expect(screen.queryByRole("heading", { name: "Invite your team" })).toBeNull();
  expect((screen.getByRole("button", { name: "Gmail" }) as HTMLButtonElement).disabled).toBe(false);
});
