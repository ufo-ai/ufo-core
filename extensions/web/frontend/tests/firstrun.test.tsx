import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, onTestFinished, test, vi } from "vitest";

import { App } from "@/App";

import {
  AGENT,
  AGENT_ID,
  CONVO_ID,
  MEMBER,
  TURN_ID,
  json,
  useStreamFake,
  wire,
  type Route,
} from "./harness";

const ADMIN = { ...MEMBER, admin: true };

const SLACK_LINK = "https://slack.com/oauth/v2/authorize?state=sealed";

const OPENED = { turn_id: TURN_ID, conversation_id: CONVO_ID, title: "Setting up" };

const FIRST_RUN = {
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
    "/api/chats": () => json({ chats: [] }),
    "/workspace/first-run": () => json(payload),
    ...routes,
  });
  render(<App agents={[AGENT]} member={member} onAgents={() => {}} />);
  return wired;
}

/** The bodies of every intent the page submitted, in the order it submitted them. */
function intents(calls: { url: string; body: unknown }[]): unknown[] {
  return calls.filter((call) => call.url.includes("/intents")).map((call) => call.body);
}

/** The lane, answering each verb with what that verb's tool would. Anything unnamed applies, so a
 *  case states only the outcome it is about. */
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
      return json(outcomes[body.verb] ?? { applied: true, message: "Saved." });
    },
  };
}

/** Records the picks and lands on the step that follows them. */
async function record(...picks: string[]) {
  for (const pick of picks) await userEvent.click(await screen.findByRole("button", { name: pick }));
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
}

/** The act that commits the step being asked for. */
function commit(): HTMLButtonElement {
  return screen.getByRole("button", { name: "Continue" }) as HTMLButtonElement;
}

function toolingIntent(...providers: string[]) {
  return { verb: "record_tooling", kind: "memory", providers };
}

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
  await screen.findByRole("button", { name: "Notion" });
  expect(screen.queryByPlaceholderText("Message the app…")).toBeNull();
});

test("the address opens the first run on its own, with no query at all", async () => {
  history.replaceState(null, "", location.pathname);
  location.hash = "#/first-run";
  open();

  await screen.findByRole("button", { name: "Notion" });
  expect(location.hash).toBe("#/first-run");
});

test("the page draws no shell around the step", async () => {
  open();

  await screen.findByRole("heading", { name: "What your team uses" });
  expect(screen.queryByRole("banner")).toBeNull();
  expect(screen.queryByRole("button", { name: "Menu" })).toBeNull();
});

test("the tiles are the whole first step: no later step stands until the picks are recorded", async () => {
  open();

  await screen.findByRole("button", { name: "Slack" });
  expect(screen.queryByRole("heading", { name: "The app answers in Slack" })).toBeNull();
  expect(screen.queryByRole("heading", { name: "Invite your team" })).toBeNull();
});

test("the picks are recorded through the intent lane, and only what was picked is asked for", async () => {
  const posted = recorder();
  open({ "/intents": posted.route });

  await record("Notion", "Slack");

  expect(intents(posted.calls)).toEqual([toolingIntent("notion", "slack")]);
  await screen.findByRole("heading", { name: "The app answers in Slack" });
  expect(screen.getByRole("button", { name: "Connect Slack" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Connect GitHub" })).toBeNull();
  // One step stands at a time: the answered step leaves the page rather than sitting above the
  // one being asked for.
  expect(screen.queryByRole("button", { name: "Notion" })).toBeNull();
  expect(screen.queryByRole("heading", { name: "What your team uses" })).toBeNull();
});

test("picking neither connector skips to the invite step", async () => {
  const posted = recorder();
  open({ "/intents": posted.route });

  await record("Notion");

  await screen.findByRole("heading", { name: "Invite your team" });
  expect(screen.queryByRole("heading", { name: "The app answers in Slack" })).toBeNull();
});

test("each connector picked is its own step, in the order the tiles offer them", async () => {
  const posted = recorder();
  open({ "/intents": posted.route });

  await record("GitHub", "Slack");

  // Slack leads because the catalog does, not because it was picked second.
  await screen.findByRole("heading", { name: "The app answers in Slack" });
  expect(screen.getByRole("button", { name: "Connect Slack" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Connect GitHub" })).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Skip" }));
  await screen.findByRole("heading", { name: "The app works in your repositories" });
  expect(screen.getByRole("button", { name: "Connect GitHub" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Connect Slack" })).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Skip" }));
  await screen.findByRole("heading", { name: "Invite your team" });
  expect(screen.getByRole("button", { name: "Invite" })).toBeTruthy();
});

test("the foot carries only the acts the step has", async () => {
  const posted = recorder();
  open({ "/intents": posted.route });

  await screen.findByRole("button", { name: "Notion" });
  expect(screen.queryByRole("button", { name: "Back" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Skip" })).toBeNull();
  expect(commit().disabled).toBe(false);

  await record("Slack");

  await screen.findByRole("heading", { name: "The app answers in Slack" });
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
  const posted = recorder({ connect_slack: { applied: true, message: "", url: SLACK_LINK } });
  open({ "/intents": posted.route });

  await record("Slack", "GitHub");

  await screen.findByRole("heading", { name: "The app answers in Slack" });
  expect(commit().disabled).toBe(true);
  await userEvent.click(screen.getByRole("button", { name: "Connect Slack" }));

  // The member has been handed the install page and has not been to it: the workspace holds
  // nothing yet, and the step says so.
  await screen.findByRole("link", { name: "Open the Slack install page" });
  await returning();
  expect(commit().disabled).toBe(true);
  expect(screen.getByRole("heading", { name: "The app answers in Slack" })).toBeTruthy();
});

test("one press opens the consent window and lands the minted link in it", async () => {
  const posted = recorder({ connect_slack: { applied: true, message: "", url: SLACK_LINK } });
  open({ "/intents": posted.route });
  const consent = { focus: vi.fn(), close: vi.fn(), location: { href: "" } };
  const opened = vi.spyOn(window, "open").mockReturnValue(consent as unknown as Window);

  await record("Slack", "GitHub");
  await screen.findByRole("heading", { name: "The app answers in Slack" });
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
  const posted = recorder({ connect_slack: { applied: true, message: "", url: SLACK_LINK } });
  open({ "/intents": posted.route });
  const opened = vi.spyOn(window, "open").mockReturnValue(null);

  await record("Slack", "GitHub");
  await screen.findByRole("heading", { name: "The app answers in Slack" });
  await userEvent.click(screen.getByRole("button", { name: "Connect Slack" }));

  const link = await screen.findByRole("link", { name: "Open the Slack install page" });
  expect(link.getAttribute("href")).toBe(SLACK_LINK);
  opened.mockRestore();
});

test("a refused connect closes the window it opened rather than parking it on nothing", async () => {
  const posted = recorder({
    connect_slack: { applied: false, message: "A workspace admin connects Slack." },
  });
  open({ "/intents": posted.route });
  const consent = { focus: vi.fn(), close: vi.fn(), location: { href: "" } };
  const opened = vi.spyOn(window, "open").mockReturnValue(consent as unknown as Window);

  await record("Slack", "GitHub");
  await screen.findByRole("heading", { name: "The app answers in Slack" });
  await userEvent.click(screen.getByRole("button", { name: "Connect Slack" }));

  await waitFor(() => expect(consent.close).toHaveBeenCalled());
  expect(consent.location.href).toBe("");
  opened.mockRestore();
});

test("the connect step passes itself when the install lands", async () => {
  const posted = recorder({ connect_slack: { applied: true, message: "", url: SLACK_LINK } });
  let landed = false;
  open({
    "/intents": posted.route,
    "/workspace/first-run": () => json(landed ? HELD_SLACK : FIRST_RUN),
  });

  await record("Slack", "GitHub");
  await screen.findByRole("heading", { name: "The app answers in Slack" });
  await userEvent.click(screen.getByRole("button", { name: "Connect Slack" }));
  await screen.findByRole("link", { name: "Open the Slack install page" });

  landed = true;
  await returning();

  // Nothing was pressed: the member finished on Slack's pages and comes back to the next question.
  await screen.findByRole("heading", { name: "The app works in your repositories" });
  expect(commit().disabled).toBe(true);
});

test("a connector the workspace already held waits to be read rather than passing itself", async () => {
  const posted = recorder();
  open({ "/intents": posted.route }, ADMIN, HELD_SLACK);

  await record("Slack");
  await screen.findByText("Slack connected");
  await returning();

  expect(screen.getByRole("heading", { name: "The app answers in Slack" })).toBeTruthy();
  expect(commit().disabled).toBe(false);
});

test("a step passed by its own install is connected on the way back to it", async () => {
  const posted = recorder({ connect_slack: { applied: true, message: "", url: SLACK_LINK } });
  let landed = false;
  open({
    "/intents": posted.route,
    "/workspace/first-run": () => json(landed ? HELD_SLACK : FIRST_RUN),
  });

  await record("Slack", "GitHub");
  await userEvent.click(await screen.findByRole("button", { name: "Connect Slack" }));
  await screen.findByRole("link", { name: "Open the Slack install page" });
  landed = true;
  await returning();
  await screen.findByRole("heading", { name: "The app works in your repositories" });
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
  const posted = recorder({ connect_slack: { applied: true, message: "", url: SLACK_LINK } });
  let landed = false;
  open({
    "/intents": posted.route,
    "/workspace/first-run": () => json(landed ? HELD_SLACK : FIRST_RUN),
  });
  const settle = () => act(async () => void (await vi.advanceTimersByTimeAsync(0)));

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
  expect(screen.getByRole("heading", { name: "The app works in your repositories" })).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "Back" }));
  await settle();

  expect(screen.getByText("Slack connected")).toBeTruthy();
  expect(commit().disabled).toBe(false);
});

test("skip leaves the connect step behind with nothing connected", async () => {
  const posted = recorder();
  open({ "/intents": posted.route });

  await record("Slack");
  await userEvent.click(await screen.findByRole("button", { name: "Skip" }));

  await screen.findByRole("heading", { name: "Invite your team" });
  expect(intents(posted.calls)).toEqual([toolingIntent("slack")]);
});

test("back re-opens the tiles with the picks still on them and still editable", async () => {
  const posted = recorder();
  open({ "/intents": posted.route });

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
  open({ "/intents": posted.route });

  await record("Slack");
  await userEvent.click(await screen.findByRole("button", { name: "Back" }));
  await userEvent.click(await screen.findByRole("button", { name: "GitHub" }));
  await userEvent.click(commit());

  await screen.findByRole("button", { name: "Connect Slack" });
  expect(intents(posted.calls)).toEqual([toolingIntent("slack"), toolingIntent("slack", "github")]);
});

test("picks left as they were on the way back are not recorded twice", async () => {
  const posted = recorder();
  open({ "/intents": posted.route });

  await record("Slack");
  await userEvent.click(await screen.findByRole("button", { name: "Back" }));
  await userEvent.click(commit());

  await screen.findByRole("button", { name: "Connect Slack" });
  expect(intents(posted.calls)).toEqual([toolingIntent("slack")]);
});

test("a connector the workspace already holds states so and carries no act", async () => {
  const posted = recorder();
  open({ "/intents": posted.route }, ADMIN, HELD_SLACK);

  await record("Slack");

  await screen.findByText("Slack connected");
  expect(screen.queryByRole("button", { name: "Connect Slack" })).toBeNull();
  expect(commit().disabled).toBe(false);
});

test("connecting mints the link on the intent lane, never through the chat", async () => {
  const posted = recorder({ connect_slack: { applied: true, message: "", url: SLACK_LINK } });
  open({ "/intents": posted.route });

  await record("Slack");
  await userEvent.click(await screen.findByRole("button", { name: "Connect Slack" }));

  const link = await screen.findByRole("link", { name: "Open the Slack install page" });
  expect(link.getAttribute("href")).toBe(SLACK_LINK);
  expect(intents(posted.calls).at(-1)).toEqual({ verb: "connect_slack" });
  expect(location.hash).toBe("#/first-run");
  expect(screen.queryByPlaceholderText("Message the app…")).toBeNull();
});

test("a link minted on one connector's step is not shown on the next one's", async () => {
  const posted = recorder({ connect_slack: { applied: true, message: "", url: SLACK_LINK } });
  open({ "/intents": posted.route });

  await record("Slack", "GitHub");
  await userEvent.click(await screen.findByRole("button", { name: "Connect Slack" }));
  await screen.findByRole("link", { name: "Open the Slack install page" });
  await userEvent.click(screen.getByRole("button", { name: "Skip" }));

  await screen.findByRole("button", { name: "Connect GitHub" });
  expect(screen.queryByRole("link")).toBeNull();
});

test("the GitHub row submits its own connect verb", async () => {
  const posted = recorder({
    connect_github: { applied: true, message: "", url: "https://github.com/apps/ufo-ai" },
  });
  open({ "/intents": posted.route });

  await record("GitHub");
  await userEvent.click(await screen.findByRole("button", { name: "Connect GitHub" }));

  await screen.findByRole("link", { name: "Open the GitHub install page" });
  expect(intents(posted.calls).at(-1)).toEqual({ verb: "connect_github" });
});

test("a connect the tool minted no link for states what it answered instead", async () => {
  const posted = recorder({
    connect_slack: {
      applied: true,
      message: "Ask a workspace admin to connect Slack — only they can install it.",
      url: null,
    },
  });
  open({ "/intents": posted.route });

  await record("Slack");
  await userEvent.click(await screen.findByRole("button", { name: "Connect Slack" }));

  await screen.findByText("Ask a workspace admin to connect Slack — only they can install it.");
  expect(screen.queryByRole("link")).toBeNull();
  expect(commit().disabled).toBe(true);
});

test("a member who is not an admin is told who installs, and presses nothing", async () => {
  const posted = recorder();
  open({ "/intents": posted.route }, MEMBER);

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
  open({ "/intents": posted.route });

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
  open({ "/intents": posted.route });

  await record("Notion");

  const first = await screen.findByLabelText("Email 1");
  await waitFor(() => expect(document.activeElement).toBe(first));
});

test("every address written is added on one press, and then the chat opens", async () => {
  const sent: string[] = [];
  const posted = recorder();
  open({
    "/intents": posted.route,
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
      { verb: "add_member", email: "sam@work.com", admin: false },
      { verb: "add_member", email: "alex@work.com", admin: false },
    ]),
  );
  await waitFor(() => expect(sent).toEqual(["We use Notion. What could you set up for us?"]));
});

test("a refused invite states the refusal and adds nobody", async () => {
  const posted = recorder({
    add_member: { applied: false, message: "only a workspace admin can add members" },
  });
  open({ "/intents": posted.route });

  await record("Notion");
  await userEvent.type(await screen.findByLabelText("Email 1"), "teammate@work.com");
  await userEvent.click(screen.getByRole("button", { name: "Invite" }));

  await screen.findByText("only a workspace admin can add members");
  expect((screen.getByLabelText("Email 1") as HTMLInputElement).value).toBe("teammate@work.com");
  // The refusal stops the run where it happened rather than handing the member on.
  expect(screen.queryByPlaceholderText("Message the app…")).toBeNull();
});

test("the last act says the picks and the question into the agent's new chat", async () => {
  const sent: { url: string; body: string }[] = [];
  const posted = recorder();
  open({
    "/intents": posted.route,
    "/chat": (url, init) => {
      sent.push({ url, body: String(init?.body) });
      return json(OPENED);
    },
  });

  // Picked out of catalog order: the message states them in the order the tiles are offered, so
  // one pick set produces one message however the member clicked it.
  await record("Notion", "Gmail");
  await userEvent.click(await screen.findByRole("button", { name: "Skip" }));

  await waitFor(() => expect(sent.length).toBe(1));
  expect(sent[0].url).toBe("/surface/web/agents/" + AGENT_ID + "/chat?conversation=new");
  expect(sent[0].body).toBe("We use Gmail, Notion. What could you set up for us?");
  const box = await screen.findByPlaceholderText("Message the app…");
  expect((box as HTMLTextAreaElement).value).toBe("");
  // The page creates no agent: creating one takes a speaking member, and the page never speaks.
  expect(intents(posted.calls)).toEqual([toolingIntent("notion", "gmail")]);
});

test("picking nothing records nothing and still says the same question", async () => {
  const sent: string[] = [];
  const posted = recorder();
  open({
    "/intents": posted.route,
    "/chat": (_url, init) => {
      sent.push(String(init?.body));
      return json(OPENED);
    },
  });

  await userEvent.click(await screen.findByRole("button", { name: "Continue" }));
  await screen.findByRole("heading", { name: "Invite your team" });
  await userEvent.click(screen.getByRole("button", { name: "Skip" }));

  await waitFor(() => expect(sent).toEqual(["What could you set up for us?"]));
  expect(intents(posted.calls)).toEqual([]);
});

test("a refused record keeps the member on the first step and states the refusal", async () => {
  open({ "/intents": () => json({ applied: false, message: "No memory extension." }) });

  await record("Gmail");

  await screen.findByText("No memory extension.");
  expect(location.hash).toBe("#/first-run");
  expect(screen.queryByRole("heading", { name: "Invite your team" })).toBeNull();
  expect((screen.getByRole("button", { name: "Gmail" }) as HTMLButtonElement).disabled).toBe(false);
});
