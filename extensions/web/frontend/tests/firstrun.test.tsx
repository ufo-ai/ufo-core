import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import { AGENT, AGENT_ID, MEMBER, json, useStreamFake, wire, type Route } from "./harness";

const ADMIN = { ...MEMBER, admin: true };

const SLACK_LINK = "https://slack.com/oauth/v2/authorize?state=sealed";

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

test("the card's query lands on the first run's own address", async () => {
  open();

  await waitFor(() => expect(location.hash).toBe("#/first-run"));
  await screen.findByRole("button", { name: "Notion" });
  expect(screen.queryByPlaceholderText("Message the agent…")).toBeNull();
});

test("the address opens the first run on its own, with no query at all", async () => {
  history.replaceState(null, "", location.pathname);
  location.hash = "#/first-run";
  open();

  await screen.findByRole("button", { name: "Notion" });
  expect(location.hash).toBe("#/first-run");
});

test("the tiles are the whole first step: no later step stands until the picks are recorded", async () => {
  open();

  await screen.findByRole("button", { name: "Slack" });
  expect(screen.queryByRole("heading", { name: "Connect Slack" })).toBeNull();
  expect(screen.queryByRole("heading", { name: "Connect GitHub" })).toBeNull();
  expect(screen.queryByRole("heading", { name: "Invite your team" })).toBeNull();
});

test("the picks are recorded through the intent lane, and only what was picked is asked for", async () => {
  const posted = recorder();
  open({ "/intents": posted.route });

  await record("Notion", "Slack");

  expect(intents(posted.calls)).toEqual([
    { verb: "record_tooling", kind: "memory", providers: ["notion", "slack"] },
  ]);
  await screen.findByRole("heading", { name: "Connect Slack" });
  expect(screen.queryByRole("heading", { name: "Connect GitHub" })).toBeNull();
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
  expect(screen.queryByRole("heading", { name: "Connect Slack" })).toBeNull();
  expect(screen.queryByRole("heading", { name: "Connect GitHub" })).toBeNull();
});

test("both connectors picked are asked for one after the other, then the invite", async () => {
  const posted = recorder();
  open({ "/intents": posted.route });

  await record("Slack", "GitHub");

  await screen.findByRole("heading", { name: "Connect Slack" });
  expect(screen.queryByRole("heading", { name: "Connect GitHub" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  await screen.findByRole("heading", { name: "Connect GitHub" });
  expect(screen.queryByRole("heading", { name: "Invite your team" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  await screen.findByRole("heading", { name: "Invite your team" });
});

test("a connector the workspace already holds states so and carries no act", async () => {
  const posted = recorder();
  open({ "/intents": posted.route }, ADMIN, HELD_SLACK);

  await record("Slack");

  const step = (await screen.findByRole("heading", { name: "Connect Slack" })).closest("section");
  expect(step?.textContent).toContain("Connected");
  expect(screen.queryByRole("button", { name: "Connect Slack" })).toBeNull();
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
  expect(screen.queryByPlaceholderText("Message the agent…")).toBeNull();
});

test("the GitHub step submits its own connect verb", async () => {
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
});

test("a member who is not an admin is told who installs, and presses nothing", async () => {
  const posted = recorder();
  open({ "/intents": posted.route }, MEMBER);

  await record("Slack");

  await screen.findByText("A workspace admin connects Slack.");
  expect(screen.queryByRole("button", { name: "Connect Slack" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  await screen.findByText("A workspace admin adds members.");
  expect(screen.queryByLabelText("Email")).toBeNull();
  expect(intents(posted.calls)).toEqual([
    { verb: "record_tooling", kind: "memory", providers: ["slack"] },
  ]);
});

test("the invite step adds a teammate through the member verb", async () => {
  const posted = recorder({ add_member: { applied: true, message: "teammate@work.com added." } });
  open({ "/intents": posted.route });

  await record("Notion");
  await userEvent.type(await screen.findByLabelText("Email"), "teammate@work.com");
  await userEvent.click(screen.getByRole("button", { name: "Add" }));

  await waitFor(() =>
    expect(intents(posted.calls).at(-1)).toEqual({
      verb: "add_member",
      email: "teammate@work.com",
      admin: false,
    }),
  );
  expect((screen.getByLabelText("Email") as HTMLInputElement).value).toBe("");
  // The member reads what landed before the page hands them on.
  await screen.findByText("Added teammate@work.com.");
});

test("a refused invite states the refusal and adds nobody", async () => {
  const posted = recorder({
    add_member: { applied: false, message: "only a workspace admin can add members" },
  });
  open({ "/intents": posted.route });

  await record("Notion");
  await userEvent.type(await screen.findByLabelText("Email"), "teammate@work.com");
  await userEvent.click(screen.getByRole("button", { name: "Add" }));

  await screen.findByText("only a workspace admin can add members");
  expect((screen.getByLabelText("Email") as HTMLInputElement).value).toBe("teammate@work.com");
});

test("the last continue opens the chat carrying the picks and the question, unsent", async () => {
  const chats: string[] = [];
  const posted = recorder();
  open({
    "/intents": posted.route,
    [`/agents/${AGENT_ID}/chat`]: (url) => {
      chats.push(url);
      return json({});
    },
  });

  // Picked out of catalog order: the message states them in the order the tiles are offered, so
  // one pick set produces one message however the member clicked it.
  await record("Notion", "Gmail");
  await userEvent.click(await screen.findByRole("button", { name: "Continue" }));

  await waitFor(() => expect(location.hash).toBe("#/new/" + AGENT_ID));
  const box = await screen.findByPlaceholderText("Message the agent…");
  expect((box as HTMLTextAreaElement).value).toBe(
    "We use Gmail, Notion. What could you set up for us?",
  );
  expect(chats).toEqual([]);
  // The page creates no agent: creating one takes a speaking member, and the page never speaks.
  expect(intents(posted.calls)).toEqual([
    { verb: "record_tooling", kind: "memory", providers: ["notion", "gmail"] },
  ]);
});

test("picking nothing records nothing and still opens the same question", async () => {
  const posted = recorder();
  open({ "/intents": posted.route });

  await userEvent.click(await screen.findByRole("button", { name: "Continue" }));
  await screen.findByRole("heading", { name: "Invite your team" });
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));

  await waitFor(() => expect(location.hash).toBe("#/new/" + AGENT_ID));
  const box = await screen.findByPlaceholderText("Message the agent…");
  expect((box as HTMLTextAreaElement).value).toBe("What could you set up for us?");
  expect(intents(posted.calls)).toEqual([]);
});

test("a refused record keeps the member on the first step and states the refusal", async () => {
  open({ "/intents": () => json({ applied: false, message: "No memory extension." }) });

  await record("Gmail");

  await screen.findByText("No memory extension.");
  expect(location.hash).toBe("#/first-run");
  expect(screen.queryByRole("heading", { name: "Invite your team" })).toBeNull();
  expect((screen.getByRole("button", { name: "Gmail" }) as HTMLButtonElement).disabled).toBe(
    false,
  );
});
