import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

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
  conversationObject,
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
  return [...(home.querySelectorAll("tbody tr") as NodeListOf<HTMLElement>)].map(
    (held) => within(held).getAllByRole("cell")[0].textContent,
  );
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
  ).toEqual(["Chat", "Owner", "Time", ""]);
  expect(cells(/Pick one thread/)[1]).toBe("M");
  expect(cells(/Deploy question/)[1]).toBe("D");
  expect(cells(/Migration run/)[1]).toBe("S");
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

test("the table stands in state order: working, then waiting, then unread, then the rest", async () => {
  wire({
    ...chatsOnWire([WORKSPACE_CHAT, UNREAD, COLLEAGUE, CHAT_ROW]),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  expect(titles(home)).toEqual([
    "Deploy question",
    "Migration run",
    "Unanswered thread",
    "Pick one thread",
  ]);
});

/** `Pick one thread` is the newer of the two, so a run ordered by recency alone puts it first and
 *  only the state rank lifts the working chat above it. */
test("recency orders a state's own run, under the state that leads it", async () => {
  wire({ ...chatsOnWire([CHAT_ROW, COLLEAGUE]), "/transcript": () => json({ messages: [] }) });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  expect(titles(home)).toEqual(["Deploy question", "Pick one thread"]);

  await act(async () => railActivity(COLLEAGUE_ID, "idle"));

  expect(state(/Deploy question/)).toBe("idle");
  expect(titles(home)).toEqual(["Pick one thread", "Deploy question"]);
});

test("a channel is one glyph beside the title, drawn for every surface but the portal", async () => {
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
  expect(within(row(/Pick one thread/)).queryByRole("img", { name: /Web|#/ })).toBeNull();
  expect(within(row(/Index rebuild/)).getByRole("img", { name: "Terminal" })).toBeTruthy();
  expect(within(home).queryByText("Web")).toBeNull();
  expect(within(home).queryByText("Portal")).toBeNull();
  expect(within(home).getAllByRole("columnheader").map((head) => head.textContent)).not.toContain(
    "Channel",
  );

  await userEvent.hover(within(row(/Standup notes/)).getByRole("img", { name: "#eng" }));
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
  await userEvent.hover(within(row(/Standup notes/)).getByRole("img", { name: "#eng" }));

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

test("the owner column is the monogram alone, and the address stands above it", async () => {
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
  expect([...owners].sort()).toEqual(["D", "M", "S"]);
  expect(within(home).queryByText("dana@example.com")).toBeNull();

  await userEvent.hover(within(row(/Deploy question/)).getByLabelText("Dana Reed"));
  const tip = await screen.findByRole("tooltip");
  expect(tip.getAttribute("data-side")).toBe("top");
  expect(tip.textContent).toBe("Dana Reed · dana@example.com");
});

test("a row with no name for its owner names the address alone above the monogram", async () => {
  wire({ ...chatsOnWire([WORKSPACE_CHAT]), "/transcript": () => json({ messages: [] }) });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByRole("region", { name: "Home" });
  await userEvent.hover(within(row(/Migration run/)).getByLabelText("sam@example.com"));

  expect((await screen.findByRole("tooltip")).textContent).toBe("sam@example.com");
});

test("every owner is drawn in one ink, whoever they are", async () => {
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
  const ink = (name: RegExp) =>
    within(row(name))
      .getAllByRole("cell")[1]
      .querySelector("[data-slot=avatar-fallback]")
      ?.getAttribute("class");
  expect(ink(/Pick one thread/)).toBe(ink(/Second thread/));
  expect(ink(/Pick one thread/)).not.toMatch(/bg-member/);
});

test("the filter bar narrows the table by scope and by what is typed, through the address", async () => {
  wire({
    ...chatsOnWire([CHAT_ROW, COLLEAGUE, WORKSPACE_CHAT]),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  expect(titles(home)).toEqual(["Deploy question", "Migration run", "Pick one thread"]);

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
  expect(titles(home)).toEqual(["Deploy question", "Pick one thread"]);
  expect(within(home).queryByRole("button", { name: "Clear search" })).toBeNull();

  await userEvent.type(field, "dana{enter}");
  expect(titles(home)).toEqual(["Deploy question"]);

  await userEvent.type(field, "{Escape}");
  expect(location.hash).toBe("#/chats");
  expect(titles(home)).toEqual(["Deploy question", "Pick one thread"]);
});

test("the filter menu narrows the table to one owner, and to one channel, through the address", async () => {
  const slack: Conversation = {
    ...CHAT_ROW,
    conversation_id: THIRD_ID,
    title: "Standup notes",
    surface: "slack",
    surface_label: "#eng",
  };
  wire({
    ...chatsOnWire([CHAT_ROW, COLLEAGUE, WORKSPACE_CHAT, slack]),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  await userEvent.click(within(home).getByRole("button", { name: "Filter" }));
  await userEvent.click(await screen.findByRole("menuitemradio", { name: "dana@example.com" }));

  expect(location.hash).toBe("#/chats?chip=owner%3Adana%40example.com");
  expect(titles(home)).toEqual(["Deploy question"]);

  await userEvent.click(within(home).getByRole("button", { name: "Filter" }));
  await userEvent.click(await screen.findByRole("menuitemradio", { name: "Slack" }));

  expect(location.hash).toBe("#/chats?chip=channel%3ASlack");
  expect(titles(home)).toEqual(["Standup notes"]);

  await userEvent.click(within(home).getByRole("button", { name: "Filter" }));
  await userEvent.click(await screen.findByRole("menuitemradio", { name: "All" }));

  expect(location.hash).toBe("#/chats");
  expect(titles(home).length).toBe(4);
});

test("the member's own address is `You` in the filter menu, and the scope narrows what it offers", async () => {
  wire({
    ...chatsOnWire([CHAT_ROW, COLLEAGUE, WORKSPACE_CHAT]),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  await userEvent.click(within(home).getByRole("button", { name: "Filter" }));
  expect(await screen.findByRole("menuitemradio", { name: "You" })).toBeTruthy();

  await userEvent.keyboard("{Escape}");
  await userEvent.click(within(home).getByRole("tab", { name: "Workspace" }));
  await userEvent.click(within(home).getByRole("button", { name: "Filter" }));

  expect(screen.queryByRole("menuitemradio", { name: "You" })).toBeNull();
  expect(await screen.findByRole("menuitemradio", { name: "dana@example.com" })).toBeTruthy();
});

test("every portal surface is one Web channel, whichever extension opened it", async () => {
  const extension: Conversation = {
    ...CHAT_ROW,
    conversation_id: THIRD_ID,
    title: "Coding session",
    surface: "extension:coding",
  };
  const slack: Conversation = {
    ...CHAT_ROW,
    conversation_id: SECOND_ID,
    title: "Standup notes",
    surface: "slack",
  };
  wire({
    ...chatsOnWire([CHAT_ROW, extension, slack]),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  await userEvent.click(within(home).getByRole("button", { name: "Filter" }));

  expect(await screen.findAllByRole("menuitemradio", { name: "Web" })).toHaveLength(1);

  await userEvent.click(screen.getByRole("menuitemradio", { name: "Web" }));

  expect(location.hash).toBe("#/chats?chip=channel%3AWeb");
  expect(titles(home)).toEqual(["Pick one thread", "Coding session"]);
});

test("a held filter the narrowed rows no longer hold still stands in the menu", async () => {
  wire({
    ...chatsOnWire([CHAT_ROW, COLLEAGUE]),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/chats?chip=owner%3Adana%40example.com";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  expect(titles(home)).toEqual(["Deploy question"]);

  await userEvent.click(within(home).getByRole("tab", { name: "Mine" }));
  expect(await within(home).findByText("No conversations match.")).toBeTruthy();

  await userEvent.click(within(home).getByRole("button", { name: "Filter" }));
  const held = await screen.findByRole("menuitemradio", { name: "dana@example.com" });
  expect(held.getAttribute("aria-checked")).toBe("true");
});

test("a filter the address names and no row holds leaves the table saying so", async () => {
  wire({ ...chatsOnWire([CHAT_ROW]), "/transcript": () => json({ messages: [] }) });
  location.hash = "#/chats?chip=owner%3Agone%40example.com";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  expect(await within(home).findByText("No conversations match.")).toBeTruthy();
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
  expect(titles(home)).toEqual(["Deploy question", "Pick one thread"]);
});

test("the share and automation marks stand together at the chat column's own right edge", async () => {
  const automated: Conversation = {
    ...CHAT_ROW,
    conversation_id: SECOND_ID,
    title: "Digest the night's changes",
    audience: "shared",
    automation_kind: "scheduled_task",
    automation_name: "nightly-digest",
    automation_title: "Digest the night's changes",
  };
  wire({ ...chatsOnWire([automated]), "/transcript": () => json({ messages: [] }) });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByRole("region", { name: "Home" });
  const held = within(row(/Digest the night's changes/));
  const marks = held.getByRole("img", { name: "Automation" }).parentElement;
  expect(marks?.getAttribute("class")).toContain("ml-auto");
  expect(held.getByRole("img", { name: "Shared with Workspace" }).parentElement).toBe(marks);
});

test("a shared chat says so above its own mark, and a private one draws none", async () => {
  wire({
    ...chatsOnWire([CHAT_ROW, WORKSPACE_CHAT]),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByRole("region", { name: "Home" });
  expect(
    within(row(/Pick one thread/)).queryByRole("img", { name: "Shared with Workspace" }),
  ).toBeNull();

  await userEvent.hover(
    within(row(/Migration run/)).getByRole("img", { name: "Shared with Workspace" }),
  );

  const tip = await screen.findByRole("tooltip");
  expect(tip.getAttribute("data-side")).toBe("top");
  expect(tip.textContent).toBe("Shared with Workspace");
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
      if (url.includes("archived=true")) return json({ objects: [] });
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
});

test("a search that matches more than one page offers the step to the rest", async () => {
  const older: Conversation = { ...CHAT_ROW, conversation_id: THIRD_ID, title: "Second page thread" };
  const reads: string[] = [];
  wire({
    "/objects/conversation$": (url) => {
      reads.push(url);
      if (url.includes("archived=true")) return json({ objects: [] });
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
  expect(within(home).getByRole("button", { name: "First" })).toHaveProperty("disabled", true);

  await userEvent.click(within(home).getByRole("button", { name: "Next" }));

  expect(location.hash).toBe("#/chats?after=page-two&q=thread");
  expect(await within(home).findByRole("row", { name: /Second page thread/ })).toBeTruthy();
  expect(reads.some((url) => url.includes("cursor=page-two"))).toBe(true);
  expect(within(home).getByRole("button", { name: "Next" })).toHaveProperty("disabled", true);

  await userEvent.click(within(home).getByRole("button", { name: "First" }));

  expect(location.hash).toBe("#/chats?q=thread");
  expect(await within(home).findByRole("row", { name: /Pick one thread/ })).toBeTruthy();
});

test("a listing the workspace holds more than says so rather than truncating in silence", async () => {
  wire({
    "/objects/conversation$": (url) =>
      url.includes("archived=true")
        ? json({ objects: [] })
        : json({ objects: [conversationObject(CHAT_ROW)], next_cursor: null, cut: true }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  expect(await within(home).findByText("Search to reach older chats.")).toBeTruthy();
});

test("a listing holding a step to the rest is not cut, because the step is the way to them", async () => {
  wire({
    "/objects/conversation$": (url) =>
      url.includes("archived=true")
        ? json({ objects: [] })
        : json({
            objects: [
              conversationObject(
                url.includes("cursor=")
                  ? { ...CHAT_ROW, conversation_id: SECOND_ID, title: "Second page thread" }
                  : CHAT_ROW,
              ),
            ],
            next_cursor: url.includes("cursor=") ? null : "page-two",
            cut: true,
          }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/chats?q=thread";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  expect(await within(home).findByRole("button", { name: "Next" })).toBeTruthy();
  expect(within(home).queryByText("Search to reach older chats.")).toBeNull();
});

test("a listing that fits on one page draws no steps under it", async () => {
  wire({ ...chatsOnWire([CHAT_ROW]), "/transcript": () => json({ messages: [] }) });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  expect(within(home).queryByRole("button", { name: "Next" })).toBeNull();
  expect(home.querySelector("tfoot")).toBeNull();
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

const FILING_ACTIONS = [
  "archive_conversation",
  "unarchive_conversation",
  "pin_conversation",
  "unpin_conversation",
  "delete_conversation",
  "restore_conversation",
];

/** The conversation kind's own action projection for one row, as the portal reads it before
 *  dispatching an act the page did not declare itself. */
function filingActions(url: string) {
  const conversationId = url.split("/actions/conversation/")[1].split("?")[0];
  return json({
    actions: FILING_ACTIONS.map((name) => ({
      name,
      description: "",
      input_schema: { properties: {} },
      call: { kind: "conversation", name: conversationId, action: name },
      label: name,
    })),
  });
}

const PINNED: Conversation = {
  ...CHAT_ROW,
  conversation_id: THIRD_ID,
  title: "Ship the plan",
  last_at: "2026-07-20T08:00:00.000Z",
  pinned: true,
};

const FILED: Conversation = {
  ...CHAT_ROW,
  conversation_id: ALERT_ID,
  title: "Old runbook",
  last_at: "2026-07-10T08:00:00.000Z",
  archived: true,
};

/** The acts land on the rows the reads answer from, so a filed conversation leaves the listing the
 *  way the workspace's own reads report it. */
function filingWire(rows: Conversation[], posted: string[]) {
  return {
    ...chatsOnWire(rows),
    "/actions/conversation/": (url: string, init?: RequestInit) => {
      if (init?.method !== "POST") return filingActions(url);
      const filed = url.split("/actions/conversation/")[1];
      posted.push(filed);
      const [conversationId, action] = filed.split("/");
      const held = rows.findIndex((row) => row.conversation_id === conversationId);
      if (action === "delete_conversation") rows.splice(held, 1);
      if (action === "archive_conversation") rows[held] = { ...rows[held], archived: true };
      if (action === "unarchive_conversation") rows[held] = { ...rows[held], archived: false };
      return json({ applied: true, message: "Saved." });
    },
    "/transcript": () => json({ messages: [] }),
  };
}

function mainTable(home: HTMLElement): HTMLElement {
  return home.querySelectorAll("table")[0] as HTMLElement;
}

test("a chat row files its conversation away, holds it above the list, or deletes it", async () => {
  const posted: string[] = [];
  wire(filingWire([CHAT_ROW, PINNED, FILED, COLLEAGUE], posted));
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  expect(titles(home)[0]).toBe("Ship the plan");

  await userEvent.click(within(home).getByRole("button", { name: "Actions for Ship the plan" }));
  expect(await screen.findByRole("menuitem", { name: "Archive" })).toBeTruthy();
  expect(screen.getByRole("menuitem", { name: "Unpin" })).toBeTruthy();
  await userEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
  await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", {
    name: "Delete",
  }));

  expect(posted).toEqual([THIRD_ID + "/delete_conversation"]);
});

test("Archived is the category to the right of Workspace, and unarchives from its table", async () => {
  const posted: string[] = [];
  wire(filingWire([CHAT_ROW, FILED], posted));
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  expect(
    within(home)
      .getAllByRole("tab")
      .map((one) => one.textContent),
  ).toEqual(["All", "Mine", "Workspace", "Archived"]);
  expect(titles(home)).toEqual(["Pick one thread"]);

  await userEvent.click(within(home).getByRole("tab", { name: "Archived" }));

  expect(location.hash).toBe("#/chats?scope=archived");
  await vi.waitFor(() => expect(titles(home)).toEqual(["Old runbook"]));

  await userEvent.click(within(home).getByRole("button", { name: "Actions for Old runbook" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "Unarchive" }));

  await vi.waitFor(() => expect(posted).toEqual([ALERT_ID + "/unarchive_conversation"]));
});

test("a filed row leaves the main table, and stands under the Archived category", async () => {
  const posted: string[] = [];
  const rows = [CHAT_ROW, PINNED];
  wire(filingWire(rows, posted));
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  expect(titles(home)).toEqual(["Ship the plan", "Pick one thread"]);

  await userEvent.click(within(home).getByRole("button", { name: "Actions for Ship the plan" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "Archive" }));

  await vi.waitFor(() =>
    expect(within(mainTable(home)).queryByRole("row", { name: /Ship the plan/ })).toBeNull(),
  );

  await userEvent.click(within(home).getByRole("tab", { name: "Archived" }));
  await vi.waitFor(() => expect(titles(home)).toEqual(["Ship the plan"]));
  expect(screen.getAllByRole("row", { name: /Ship the plan/ })).toHaveLength(1);

  await userEvent.click(within(home).getByRole("tab", { name: "All" }));
  await userEvent.click(within(home).getByRole("button", { name: "Actions for Pick one thread" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "Delete" }));
  await userEvent.click(
    within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete" }),
  );

  await vi.waitFor(() =>
    expect(screen.queryByRole("row", { name: /Pick one thread/ })).toBeNull(),
  );
  expect(posted).toEqual([THIRD_ID + "/archive_conversation", CONVO_ID + "/delete_conversation"]);
});

test("an archived listing the workspace holds more than says so rather than truncating", async () => {
  wire({
    "/objects/conversation$": (url) =>
      url.includes("archived=true")
        ? json({ objects: [conversationObject(FILED)], next_cursor: null, cut: true })
        : json({ objects: [conversationObject(CHAT_ROW)] }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  expect(within(home).queryByText("Search to reach older chats.")).toBeNull();

  await userEvent.click(within(home).getByRole("tab", { name: "Archived" }));

  expect(await within(home).findByRole("row", { name: /Old runbook/ })).toBeTruthy();
  expect(within(home).getByText("Search to reach older chats.")).toBeTruthy();
});

test("Delete is offered on a conversation this member owns and on no other", async () => {
  const posted: string[] = [];
  wire(filingWire([CHAT_ROW, COLLEAGUE], posted));
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  await userEvent.click(within(home).getByRole("button", { name: "Actions for Deploy question" }));

  expect(await screen.findByRole("menuitem", { name: "Archive" })).toBeTruthy();
  expect(screen.getByRole("menuitem", { name: "Pin" })).toBeTruthy();
  expect(screen.queryByRole("menuitem", { name: "Delete" })).toBeNull();
});
