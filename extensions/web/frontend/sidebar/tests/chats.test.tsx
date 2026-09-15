import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import type { Conversation } from "@/lib/types";
import { railActivity } from "@/lib/railStore";

import {
  AGENT,
  AGENT_ID,
  CHAT_ROW,
  atPhoneWidth,
  CONVO_ID,
  MEMBER,
  SECOND,
  SECOND_ID,
  chatsOnWire,
  json,
  useStreamFake,
  wire,
} from "../../tests/harness";

const COLLEAGUE_ID = "66666666-6666-4666-8666-666666666666";
const THIRD_ID = "77777777-7777-4777-8777-777777777777";
const ALERT_ID = "99999999-9999-4999-8999-999999999999";

const COLLEAGUE: Conversation = {
  ...CHAT_ROW,
  conversation_id: COLLEAGUE_ID,
  title: "Deploy question",
  last_at: "2026-08-01T08:00:00.000Z",
  audience: "shared",
  member_email: null,
  owner_email: "dana@example.com",
  owner_name: "Dana Reed",
  mine: false,
  speaker: "Dana Reed (dana@example.com)",
  turn: "running",
};

const WORKSPACE_CHAT: Conversation = {
  ...CHAT_ROW,
  conversation_id: SECOND_ID,
  title: "Migration run",
  last_at: "2026-07-30T08:00:00.000Z",
  audience: "shared",
  member_email: null,
  owner_email: "sam@example.com",
  owner_name: null,
  mine: false,
  turn: "parked",
};

const UNREAD: Conversation = {
  ...CHAT_ROW,
  conversation_id: THIRD_ID,
  title: "Unanswered thread",
  last_at: "2026-07-31T08:00:00.000Z",
  unread: true,
};

function row(name: RegExp): HTMLElement {
  return screen.getByRole("row", { name });
}

function cells(name: RegExp): string[] {
  return within(row(name))
    .getAllByRole("cell")
    .map((cell) => cell.textContent ?? "");
}

function titles(home: HTMLElement): (string | null)[] {
  return within(home)
    .getAllByRole("row")
    .slice(1)
    .map((held) => within(held).getAllByRole("cell")[0].textContent);
}

function state(name: RegExp): string | null {
  return (
    within(row(name))
      .getAllByRole("cell")[0]
      .querySelector("[data-state]")
      ?.getAttribute("data-state") ?? null
  );
}

beforeEach(() => {
  location.hash = "";
  useStreamFake();
});

test("the Home row stands a table of the chats the member reaches, whoever owns them", async () => {
  wire({
    ...chatsOnWire([CHAT_ROW, COLLEAGUE, WORKSPACE_CHAT]),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Home" }));

  expect(location.hash).toBe("#/chats");
  const home = await screen.findByRole("region", { name: "Home" });
  expect(within(home).getByRole("heading", { name: "Home" })).toBeTruthy();
  expect(
    within(home)
      .getAllByRole("columnheader")
      .map((head) => head.textContent),
  ).toEqual(["Chat", "Owner", "Channel", "Time"]);
  expect(cells(/Pick one thread/).slice(1, 3)).toEqual(["Mmember@example.com", "Web"]);
  expect(cells(/Deploy question/).slice(1, 3)).toEqual(["Ddana@example.com", "Web"]);
  expect(cells(/Migration run/).slice(1, 3)).toEqual(["Ssam@example.com", "Web"]);
});

test("the status leads the chat's own cell, and holds no column of its own", async () => {
  wire({
    ...chatsOnWire([CHAT_ROW, COLLEAGUE, WORKSPACE_CHAT, UNREAD]),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  expect(within(home).queryByRole("columnheader", { name: "Status" })).toBeNull();
  expect(state(/Deploy question/)).toBe("working");
  expect(state(/Migration run/)).toBe("waiting");
  expect(state(/Unanswered thread/)).toBe("unread");
  expect(state(/Pick one thread/)).toBe("idle");
  expect(within(row(/Migration run/)).getByRole("img", { name: "Waiting for you" })).toBeTruthy();
});

test("the table stands newest first, whoever moved the chat and whatever its state", async () => {
  wire({
    ...chatsOnWire([WORKSPACE_CHAT, UNREAD, COLLEAGUE, CHAT_ROW]),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  expect(titles(home)).toEqual([
    "Pick one thread",
    "Deploy question",
    "Unanswered thread",
    "Migration run",
  ]);
});

test("a turn admitted here moves its chat to the top, and its ending leaves it there", async () => {
  wire({ ...chatsOnWire([CHAT_ROW, COLLEAGUE]), "/transcript": () => json({ messages: [] }) });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  expect(titles(home)).toEqual(["Pick one thread", "Deploy question"]);

  await act(async () => railActivity(COLLEAGUE_ID, "running"));
  expect(titles(home)).toEqual(["Deploy question", "Pick one thread"]);

  await act(async () => railActivity(COLLEAGUE_ID, "idle"));
  expect(state(/Deploy question/)).toBe("idle");
  expect(titles(home)).toEqual(["Deploy question", "Pick one thread"]);
});

test("a channel names its surface, and a Slack thread's room stands under the pointer", async () => {
  const slack: Conversation = {
    ...CHAT_ROW,
    conversation_id: SECOND_ID,
    title: "Standup notes",
    surface: "slack",
    surface_label: "#eng",
  };
  const terminal: Conversation = {
    ...CHAT_ROW,
    conversation_id: THIRD_ID,
    title: "Index rebuild",
    surface: "ufo",
    surface_label: null,
  };
  wire({
    ...chatsOnWire([CHAT_ROW, slack, terminal]),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  expect(cells(/Pick one thread/)[2]).toBe("Web");
  expect(cells(/Standup notes/)[2]).toBe("Slack");
  expect(cells(/Index rebuild/)[2]).toBe("Terminal");
  expect(within(home).queryByText("Portal")).toBeNull();

  await userEvent.hover(within(row(/Standup notes/)).getByText("Slack"));
  expect((await screen.findByRole("tooltip")).textContent).toBe("#eng");
});

test("a Slack room leads back out to its thread, above the row it names", async () => {
  const slack: Conversation = {
    ...CHAT_ROW,
    conversation_id: SECOND_ID,
    title: "Standup notes",
    surface: "slack",
    surface_label: "#eng",
    source: "https://acme.slack.com/archives/C01/p1700000000000100",
  };
  wire({ ...chatsOnWire([slack]), "/transcript": () => json({ messages: [] }) });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByRole("region", { name: "Home" });
  await userEvent.hover(within(row(/Standup notes/)).getByText("Slack"));

  const tip = await screen.findByRole("tooltip");
  expect(tip.getAttribute("data-side")).toBe("top");
  const out = within(tip).getByRole("link", { name: /#eng/ });
  expect(out.getAttribute("href")).toBe(slack.source);
});

test("an automation names itself above the row and leads to its own screen", async () => {
  const automated: Conversation = {
    ...CHAT_ROW,
    conversation_id: SECOND_ID,
    title: "Last night's digest",
    automation_kind: "scheduled_task",
    automation_name: "nightly-digest",
    automation_title: "Digest the night's changes",
  };
  wire({ ...chatsOnWire([automated]), "/transcript": () => json({ messages: [] }) });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByRole("region", { name: "Home" });
  await userEvent.hover(within(row(/Last night's digest/)).getByRole("img", { name: "Automation" }));

  const tip = await screen.findByRole("tooltip");
  expect(tip.getAttribute("data-side")).toBe("top");
  const at = within(tip).getByRole("link", { name: /Digest the night's changes/ });
  expect(at.getAttribute("href")).toBe(
    "#/automations?open=automation%2F" + AGENT_ID + "%2Fscheduled_task%2Fnightly-digest",
  );
});

test("the time column carries the stamp of the row's last activity", async () => {
  wire({ ...chatsOnWire([CHAT_ROW]), "/transcript": () => json({ messages: [] }) });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByRole("region", { name: "Home" });
  const stamp = row(/Pick one thread/).querySelector("time");
  expect(stamp?.getAttribute("datetime")).toBe(CHAT_ROW.last_at);
});

test("a press on a row opens that conversation", async () => {
  wire({ ...chatsOnWire([CHAT_ROW]), "/transcript": () => json({ messages: [] }) });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  await userEvent.click(within(home).getByRole("row", { name: /Pick one thread/ }));

  expect(location.hash).toBe("#/c/" + CONVO_ID);
});

test("a press on an app's own conversation opens that conversation, not the app's page", async () => {
  const radar = { ...SECOND, name: "radar", app: "radar" };
  const alert: Conversation = {
    ...CHAT_ROW,
    conversation_id: ALERT_ID,
    agent_id: SECOND_ID,
    agent_name: "radar",
    title: "Deploy alert",
  };
  wire({ ...chatsOnWire([CHAT_ROW, alert]), "/transcript": () => json({ messages: [] }) });
  location.hash = "#/chats";
  render(<App agents={[AGENT, radar]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  await userEvent.click(within(home).getByRole("row", { name: /Deploy alert/ }));

  expect(location.hash).toBe("#/c/" + ALERT_ID);
});

test("the table says so when the member has no conversations", async () => {
  wire({ ...chatsOnWire([]), "/transcript": () => json({ messages: [] }) });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  expect(await within(home).findByText("No conversations yet.")).toBeTruthy();
});

test("the rail keeps its own mark while the table reads the same state", async () => {
  wire({ ...chatsOnWire([CHAT_ROW]), "/transcript": () => json({ messages: [] }) });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByRole("region", { name: "Home" });
  const rail = screen.getByRole("navigation", { name: "Workspace" });
  const railMark = () =>
    within(rail)
      .getByRole("button", { name: /Pick one thread/ })
      .querySelector("[data-turn]")
      ?.getAttribute("data-turn");

  expect(railMark()).toBe("idle");
  expect(state(/Pick one thread/)).toBe("idle");

  await act(async () => railActivity(CONVO_ID, "running"));

  expect(railMark()).toBe("running");
  expect(state(/Pick one thread/)).toBe("working");
});

test("an automation states itself beside its name, and the rail filter can drop it", async () => {
  const automated: Conversation = {
    ...CHAT_ROW,
    conversation_id: SECOND_ID,
    title: "Digest the night's changes",
    automation_kind: "scheduled_task",
    automation_name: "nightly-digest",
    automation_title: "Digest the night's changes",
  };
  wire({ ...chatsOnWire([CHAT_ROW, automated]), "/transcript": () => json({ messages: [] }) });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  expect(
    within(row(/Digest the night's changes/)).getByRole("img", { name: "Automation" }),
  ).toBeTruthy();
  expect(
    within(row(/Pick one thread/)).queryByRole("img", { name: "Automation" }),
  ).toBeNull();

  const rail = screen.getByRole("navigation", { name: "Workspace" });
  await userEvent.click(within(rail).getByRole("button", { name: "Recents options" }));
  await userEvent.click(await screen.findByRole("menuitemcheckbox", { name: "Automations" }));

  expect(within(rail).queryByRole("button", { name: /Digest the night's changes/ })).toBeNull();
  expect(within(home).getByRole("row", { name: /Digest the night's changes/ })).toBeTruthy();
});

test("an owner is one coloured monogram, and the address stands under the pointer", async () => {
  wire({
    ...chatsOnWire([CHAT_ROW, COLLEAGUE, WORKSPACE_CHAT]),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  const owners = within(home)
    .getAllByRole("row")
    .slice(1)
    .map((held) => within(held).getAllByRole("cell")[1].textContent);
  expect([...owners].sort()).toEqual([
    "Ddana@example.com",
    "Mmember@example.com",
    "Ssam@example.com",
  ]);

  await userEvent.hover(within(row(/Deploy question/)).getByLabelText("Dana Reed"));
  expect((await screen.findByRole("tooltip")).textContent).toBe("Dana Reed");
});

/** Two addresses at one company differ in their first letters, which is where FNV-1a's avalanche
 *  is weakest; bucketing on the low bits put this pair on one colour. */
test("two colleagues at one company are not drawn in one colour", async () => {
  const rows: Conversation[] = [
    { ...CHAT_ROW, owner_email: "alex@simplecasual.com", owner_name: null },
    {
      ...CHAT_ROW,
      conversation_id: SECOND_ID,
      title: "Second thread",
      owner_email: "dana@simplecasual.com",
      owner_name: null,
    },
  ];
  wire({ ...chatsOnWire(rows), "/transcript": () => json({ messages: [] }) });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByRole("region", { name: "Home" });
  const hue = (name: RegExp) =>
    within(row(name))
      .getAllByRole("cell")[1]
      .querySelector("[data-slot=avatar-fallback]")
      ?.getAttribute("class");
  expect(hue(/Pick one thread/)).not.toBe(hue(/Second thread/));
  expect(hue(/Pick one thread/)).toMatch(/bg-member-[1-3]/);
});

test("the filter bar narrows the table by scope and by what is typed, through the address", async () => {
  wire({
    ...chatsOnWire([CHAT_ROW, COLLEAGUE, WORKSPACE_CHAT]),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  expect(titles(home)).toEqual(["Pick one thread", "Deploy question", "Migration run"]);

  await userEvent.click(within(home).getByRole("tab", { name: "Mine" }));
  expect(location.hash).toBe("#/chats?scope=mine");
  expect(titles(home)).toEqual(["Pick one thread"]);

  await userEvent.click(within(home).getByRole("tab", { name: "Workspace" }));
  expect(titles(home)).toEqual(["Deploy question", "Migration run"]);

  await userEvent.click(within(home).getByRole("tab", { name: "All" }));
  await userEvent.type(within(home).getByRole("searchbox", { name: "Search chats" }), "dana{enter}");

  expect(location.hash).toBe("#/chats?q=dana");
  expect(titles(home)).toEqual(["Deploy question"]);
});

test("the search is cleared by its own control, and by Escape, in one press", async () => {
  wire({
    ...chatsOnWire([CHAT_ROW, COLLEAGUE]),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/chats?q=dana";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  const field = within(home).getByRole("searchbox", { name: "Search chats" });
  expect(titles(home)).toEqual(["Deploy question"]);

  await userEvent.click(within(home).getByRole("button", { name: "Clear search" }));

  expect(location.hash).toBe("#/chats");
  expect(field).toHaveProperty("value", "");
  expect(titles(home)).toEqual(["Pick one thread", "Deploy question"]);
  expect(within(home).queryByRole("button", { name: "Clear search" })).toBeNull();

  await userEvent.type(field, "dana{enter}");
  expect(titles(home)).toEqual(["Deploy question"]);

  await userEvent.type(field, "{Escape}");
  expect(location.hash).toBe("#/chats");
  expect(titles(home)).toEqual(["Pick one thread", "Deploy question"]);
});

test("a filter that leaves nothing says so, and the bar stays put", async () => {
  wire({ ...chatsOnWire([CHAT_ROW]), "/transcript": () => json({ messages: [] }) });
  location.hash = "#/chats?q=nothing";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  expect(await within(home).findByText("No conversations match.")).toBeTruthy();
  expect(within(home).getByRole("tab", { name: "All" })).toBeTruthy();
  expect(within(home).getAllByRole("columnheader").length).toBe(4);
});

test("a phone draws the chat alone, and the columns beside it are not stacked under it", async () => {
  atPhoneWidth();
  wire({ ...chatsOnWire([CHAT_ROW, COLLEAGUE]), "/transcript": () => json({ messages: [] }) });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  const table = home.querySelector("[data-slot=table]");
  expect(table?.getAttribute("data-lede")).toBe("");
  expect(table?.getAttribute("data-stacks")).toBeNull();
  expect(titles(home)).toEqual(["Pick one thread", "Deploy question"]);
});

test("the automation mark stands at the chat column's own right edge", async () => {
  const automated: Conversation = {
    ...CHAT_ROW,
    conversation_id: SECOND_ID,
    title: "Digest the night's changes",
    automation_kind: "scheduled_task",
    automation_name: "nightly-digest",
    automation_title: "Digest the night's changes",
  };
  wire({ ...chatsOnWire([automated]), "/transcript": () => json({ messages: [] }) });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByRole("region", { name: "Home" });
  const drawn = within(row(/Digest the night's changes/)).getByRole("img", { name: "Automation" });
  expect(drawn.getAttribute("class")).toContain("ml-auto");
});

const CUT_NOTICE = "Showing your most recent conversations. Search to reach the rest.";

test("a listing the workspace holds more than says so rather than truncating in silence", async () => {
  wire({
    "/objects/conversation$": () =>
      json({ objects: [CHAT_ROW].map((row) => ({ name: row.conversation_id, ...row })), cut: true }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  expect(await within(home).findByText(CUT_NOTICE)).toBeTruthy();
});

test("a search reaches past the bound, so it never reports a chat it did not look for", async () => {
  const older: Conversation = {
    ...CHAT_ROW,
    conversation_id: THIRD_ID,
    title: "Older than the bound",
  };
  const calls: string[] = [];
  wire({
    "/objects/conversation$": (url) => {
      calls.push(url);
      const said = new URLSearchParams(url.split("?")[1] ?? "").get("q") ?? "";
      const rows = said ? [older] : [CHAT_ROW];
      return json({
        objects: rows.map((row) => ({ name: row.conversation_id, ...row })),
        cut: !said,
      });
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  expect(titles(home)).toEqual(["Pick one thread"]);

  await userEvent.type(within(home).getByRole("searchbox", { name: "Search chats" }), "older{enter}");

  expect(await within(home).findByRole("row", { name: /Older than the bound/ })).toBeTruthy();
  expect(calls.some((url) => url.includes("q=older"))).toBe(true);
  expect(within(home).queryByText(CUT_NOTICE)).toBeNull();
});

test("a search that matches more than one page offers the step to the rest", async () => {
  const older: Conversation = { ...CHAT_ROW, conversation_id: THIRD_ID, title: "Second page thread" };
  const reads: string[] = [];
  wire({
    "/objects/conversation$": (url) => {
      reads.push(url);
      const stepped = new URLSearchParams(url.split("?")[1] ?? "").get("cursor");
      const rows = stepped ? [older] : [CHAT_ROW];
      return json({
        objects: rows.map((row) => ({ name: row.conversation_id, ...row })),
        next_cursor: stepped ? null : "page-two",
        cut: false,
      });
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/chats?q=thread";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  expect(titles(home)).toEqual(["Pick one thread"]);
  // A page that holds a step to the rest is not cut; the step is the way to them.
  expect(within(home).queryByText(CUT_NOTICE)).toBeNull();

  await userEvent.click(within(home).getByRole("button", { name: "Older" }));

  expect(location.hash).toBe("#/chats?after=page-two&q=thread");
  expect(await within(home).findByRole("row", { name: /Second page thread/ })).toBeTruthy();
  expect(reads.some((url) => url.includes("cursor=page-two"))).toBe(true);
  expect(within(home).queryByRole("button", { name: "Older" })).toBeNull();
});

test("a search the rows do not exhaust still says so when the listing was cut", async () => {
  wire({
    "/objects/conversation$": () =>
      json({
        objects: [{ name: CHAT_ROW.conversation_id, ...CHAT_ROW }],
        next_cursor: null,
        cut: true,
      }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/chats?q=thread";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  expect(await within(home).findByText(CUT_NOTICE)).toBeTruthy();
});

test("a row a colleague founded opens their conversation, with the composer to speak in it", async () => {
  const { calls } = wire({
    ...chatsOnWire([CHAT_ROW, COLLEAGUE]),
    "/transcript": () => json({ messages: [{ role: "user", text: "Dana asked first" }] }),
  });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  await userEvent.click(within(home).getByRole("row", { name: /Deploy question/ }));

  expect(location.hash).toBe("#/c/" + COLLEAGUE_ID);
  expect(await screen.findByText("Dana asked first")).toBeTruthy();
  expect(screen.getByLabelText("Ask UFO")).toBeTruthy();
  expect(
    calls.some((url) =>
      url.includes("/agents/" + AGENT_ID + "/conversations/" + COLLEAGUE_ID + "/transcript"),
    ),
  ).toBe(true);
});
