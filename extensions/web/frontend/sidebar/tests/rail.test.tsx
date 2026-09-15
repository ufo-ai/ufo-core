import { readFileSync } from "node:fs";
import { join } from "node:path";

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App, NARROW } from "@/App";
import { agentName } from "@/lib/agentName";
import { RESTING_STATUS_MS, WORKING_STATUS_MS } from "@/lib/appStatusStore";
import { rowMoment } from "@/lib/moments";
import {
  appOrder,
  appRun,
  bumpChat,
  heldAppsExpanded,
  heldRailShown,
  heldRailShut,
  holdAppsExpanded,
  holdRailShown,
  holdRailShut,
  mergeChats,
  railGroups,
  type RailSort,
  railShut,
  stampIso,
  type ChatRow,
  type RailTurn,
} from "@/lib/rail";
import { pickAppsExpanded, pickRailShown, railState, resetRailStore } from "@/lib/railStore";
import type { Agent } from "@/lib/types";

import { AGENT, AGENT_ID, atPhoneWidth, CHAT_APP, CHAT_APP_ID, CHAT_ROW, chatsOnWire, conversationObject, CONVO_ID, destination, json, MEMBER, objectIndex, SECOND, SECOND_ID, SITE_KIND, StreamFake, TASK_KIND, TRIGGER_KIND, TURN_ID, useStreamFake, wire } from "./harness";

beforeEach(() => {
  useStreamFake();
});

const NOW = new Date(2026, 7, 1, 12, 0, 0);

const NOT_SHARED = "This conversation is not shared with this account.";

const RECENCY: RailSort = "recency";

const PORTAL_ONLY = { terminal: false, slack: false, imessage: false };
const EVERY_SURFACE = { terminal: true, slack: true, imessage: true };

async function hoverCard(): Promise<HTMLElement> {
  return await waitFor(() => {
    const card = document.querySelector("[data-slot=hover-card-content]");
    if (card === null) throw new Error("no hover card");
    return card as HTMLElement;
  });
}

function hoursAgo(hours: number): string {
  return new Date(NOW.getTime() - hours * 3_600_000).toISOString();
}

async function settle(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
    await Promise.resolve();
    await Promise.resolve();
  });
}

function row(id: string, last_at: string): ChatRow {
  return {
    conversation_id: id,
    agent_id: AGENT_ID,
    agent_name: "assistant",
    title: "chat " + id,
    opening: null,
    last_at,
    surface: "web",
    surface_label: null,
    audience: "member:m1",
    member_email: "member@example.com",
    mine: true,
    speaker: null,
    source: null,
    turn: "idle",
    unread: false,
  };
}

function theirs(id: string, last_at: string, speaker: string): ChatRow {
  return { ...row(id, last_at), mine: false, speaker };
}

test("the rail is one unheaded run of rows, and no run at all where none stand", () => {
  const rows = [
    row("a", hoursAgo(3)),
    row("b", hoursAgo(20)),
    row("c", hoursAgo(4 * 24)),
    row("d", hoursAgo(90 * 24)),
  ];
  expect(railGroups(rows, PORTAL_ONLY, RECENCY)).toEqual([{ label: null, rows }]);
  expect(railGroups([], PORTAL_ONLY, RECENCY)).toEqual([]);
});

test("everyone else's conversations are one group at the foot", () => {
  const rows = [
    row("a", hoursAgo(1)),
    theirs("t1", hoursAgo(2), "Pat Reyes (pat@example.com)"),
    row("b", hoursAgo(20)),
    theirs("t2", hoursAgo(30 * 24), "sam@example.com"),
  ];

  const grouped = railGroups(rows, PORTAL_ONLY, RECENCY);
  expect(grouped.map((group) => group.label)).toEqual([null, "Other members"]);
  expect(grouped[0].rows.map((entry) => entry.conversation_id)).toEqual(["a", "b"]);
  expect(grouped[1].rows.map((entry) => entry.conversation_id)).toEqual(["t1", "t2"]);

  expect(railGroups([row("a", hoursAgo(1))], PORTAL_ONLY, RECENCY).map((group) => group.label)).toEqual([
    null,
  ]);
});

function elsewhere(id: string, surface: string): ChatRow {
  return { ...row(id, hoursAgo(1)), surface, title: surface + " " + id };
}

test("the rail holds portal conversations until the filter names another surface", () => {
  const rows = [
    row("a", hoursAgo(1)),
    elsewhere("s1", "slack"),
    elsewhere("u1", "ufo"),
    { ...elsewhere("s2", "slack"), mine: false, speaker: "sam@example.com" },
  ];

  expect(railGroups(rows, PORTAL_ONLY, RECENCY)).toEqual([{ label: null, rows: [rows[0]] }]);

  const withSlack = railGroups(rows, { terminal: false, slack: true, imessage: false }, RECENCY);
  expect(withSlack.map((group) => group.label)).toEqual([null, "Other members"]);
  expect(withSlack[0].rows.map((entry) => entry.conversation_id)).toEqual(["a", "s1"]);
  expect(withSlack[1].rows.map((entry) => entry.conversation_id)).toEqual(["s2"]);

  const withTerminal = railGroups(rows, { terminal: true, slack: false, imessage: false }, RECENCY);
  expect(withTerminal.map((group) => group.rows.map((entry) => entry.conversation_id))).toEqual([
    ["a", "u1"],
  ]);

  const withBoth = railGroups(rows, EVERY_SURFACE, RECENCY);
  expect(withBoth[0].rows.map((entry) => entry.conversation_id)).toEqual(["a", "s1", "u1"]);
});

test("a browser holding no filter admits every surface, and holds what a member names", () => {
  expect(heldRailShown()).toEqual(EVERY_SURFACE);

  holdRailShown({ terminal: true, slack: false, imessage: false });
  expect(heldRailShown()).toEqual({ terminal: true, slack: false, imessage: false });

  holdRailShown(EVERY_SURFACE);
  expect(heldRailShown()).toEqual(EVERY_SURFACE);

  holdRailShown(PORTAL_ONLY);
  expect(heldRailShown()).toEqual(PORTAL_ONLY);
});

const SLACK_CHAT = {
  ...CHAT_ROW,
  conversation_id: "66666666-6666-4666-8666-666666666666",
  title: "Deploy question",
  surface: "slack",
};

const TERMINAL_CHAT = {
  ...CHAT_ROW,
  conversation_id: "77777777-7777-4777-8777-777777777777",
  title: "Migration run",
  surface: "ufo",
};

/** The composer takes focus once the rail's first read lands, and that focus move shuts an open
 *  menu, so the filter is opened only after it has settled. */
async function composerFocused() {
  await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("Ask UFO")));
}

async function filterBy(label: string) {
  await userEvent.click(await screen.findByRole("button", { name: "Chats options" }));
  await userEvent.click(await screen.findByRole("menuitemcheckbox", { name: label }));
  await userEvent.keyboard("{Escape}");
}

test("the rail walks the listing to its far page", async () => {
  const older = {
    ...CHAT_ROW,
    conversation_id: SECOND_ID,
    title: "Second page thread",
    last_at: "2026-08-01T09:00:00.000Z",
  };
  let calls = 0;
  wire({
    "/objects/conversation$": () => {
      calls += 1;
      return json(
        calls === 1
          ? { objects: [conversationObject(CHAT_ROW)], next_cursor: "walk-on" }
          : { objects: [conversationObject(older)], next_cursor: null },
      );
    },
    "/api/chats": () => json({ chats: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByRole("button", { name: /Pick one thread/ })).toBeTruthy();
  expect(await screen.findByRole("button", { name: /Second page thread/ })).toBeTruthy();
  expect(calls).toBe(2);
});

test("the rail draws its first page while the walk is still reading", async () => {
  const older = {
    ...CHAT_ROW,
    conversation_id: SECOND_ID,
    title: "Second page thread",
    last_at: "2026-08-01T09:00:00.000Z",
  };
  let release = () => {};
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let calls = 0;
  wire({
    "/objects/conversation$": async () => {
      calls += 1;
      if (calls === 1)
        return json({ objects: [conversationObject(CHAT_ROW)], next_cursor: "walk-on" });
      await held;
      return json({ objects: [conversationObject(older)], next_cursor: null });
    },
    "/api/chats": () => json({ chats: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByRole("button", { name: /Pick one thread/ })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Second page thread/ })).toBeNull();

  await act(async () => {
    release();
    await held;
  });
  expect(await screen.findByRole("button", { name: /Second page thread/ })).toBeTruthy();
});

test("the filter admits a surface into the rail and the browser keeps the choice", async () => {
  holdRailShown(PORTAL_ONLY);
  wire({ ...chatsOnWire([CHAT_ROW, SLACK_CHAT, TERMINAL_CHAT]) });
  const first = render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByRole("button", { name: /Pick one thread/ })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Deploy question/ })).toBeNull();
  expect(screen.queryByRole("button", { name: /Migration run/ })).toBeNull();
  await composerFocused();

  await filterBy("Slack");
  expect(await screen.findByRole("button", { name: /Deploy question/ })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Migration run/ })).toBeNull();

  await filterBy("Terminal");
  expect(await screen.findByRole("button", { name: /Migration run/ })).toBeTruthy();

  first.unmount();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  expect(await screen.findByRole("button", { name: /Deploy question/ })).toBeTruthy();
  expect(await screen.findByRole("button", { name: /Migration run/ })).toBeTruthy();
});

test("the drawer holds the rail at a phone width, and a pick shuts it", async () => {
  atPhoneWidth();
  wire({ ...chatsOnWire([CHAT_ROW]) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(screen.queryByRole("navigation", { name: "Workspace" })).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Menu" }));
  const drawer = await screen.findByRole("dialog");
  const rail = within(drawer).getByRole("navigation", { name: "Workspace" });
  expect(within(rail).getByRole("button", { name: "Chats options" })).toBeTruthy();

  await userEvent.click(await within(rail).findByRole("button", { name: /Pick one thread/ }));

  expect(location.hash).toBe("#/c/" + CONVO_ID);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

test("a tick leaves the filter open, so both surfaces are named in one visit", async () => {
  holdRailShown(PORTAL_ONLY);
  wire({ ...chatsOnWire([CHAT_ROW, SLACK_CHAT, TERMINAL_CHAT]) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await composerFocused();

  await userEvent.click(await screen.findByRole("button", { name: "Chats options" }));
  await userEvent.click(await screen.findByRole("menuitemcheckbox", { name: "Slack" }));
  await userEvent.click(screen.getByRole("menuitemcheckbox", { name: "Terminal" }));

  expect(screen.getByRole("button", { name: "Chats options" }).getAttribute("aria-expanded")).toBe(
    "true",
  );
  expect(
    ["Terminal", "Slack", "iMessage"].map((label) =>
      screen.getByRole("menuitemcheckbox", { name: label }).getAttribute("aria-checked"),
    ),
  ).toEqual(["true", "true", "false"]);

  expect(await screen.findByRole("button", { name: /Deploy question/ })).toBeTruthy();
  expect(await screen.findByRole("button", { name: /Migration run/ })).toBeTruthy();
});

/** The row draws a stamp beside the title, so what the row *says* is the title's own frame. */
function rowWords(row: HTMLElement): string {
  return (row.querySelector(":scope > span:not([data-turn])") as HTMLElement).textContent ?? "";
}

async function openGroup(label: string) {
  await userEvent.click(await screen.findByRole("button", { name: label, expanded: false }));
}

test("an untouched rail opens its first group and shuts the rest", () => {
  expect(heldRailShut()).toBeNull();
  expect(railShut(null, ["Assistant", "Support", "Other members"])).toEqual([
    "Support",
    "Other members",
  ]);
  expect(railShut(null, ["Assistant"])).toEqual([]);
  expect(railShut(null, [])).toEqual([]);
});

test("the unheaded run leads the rail and shuts every heading under it", () => {
  expect(railShut(null, [null, "Other members"])).toEqual(["Other members"]);
  expect(railShut(null, [null])).toEqual([]);
});

test("once a heading is clicked the member's own set governs, empty or not", () => {
  const labels = ["Assistant", "Support", "Other members"];
  expect(railShut([], labels)).toEqual([]);
  expect(railShut(["Assistant"], labels)).toEqual(["Assistant"]);
});

test("a browser keeps which groups are shut, and holding none is not holding an empty set", () => {
  expect(heldRailShut()).toBeNull();

  holdRailShut(["Support", "Other members"]);
  expect(heldRailShut()).toEqual(["Support", "Other members"]);

  holdRailShut([]);
  expect(heldRailShut()).toEqual([]);
});

test("an agent name carrying a comma survives the round trip", () => {
  holdRailShut(["Reyes, Pat", "Other members"]);
  expect(heldRailShut()).toEqual(["Reyes, Pat", "Other members"]);
});

function app(id: string, name: string): Agent {
  return { ...AGENT, id, name, main: false };
}

function ids(apps: Agent[]): string[] {
  return apps.map((one) => one.id);
}

const WORKED_AT: Record<string, number> = { radar: 300, wiki: 200, brief: 100 };

function worked(agentId: string): number | null {
  return WORKED_AT[agentId] ?? null;
}

test("pinned apps stand in the order the member pinned them, whatever their names", () => {
  const apps = [app("brief", "Brief"), app("radar", "Radar"), app("wiki", "Wiki")];
  expect(ids(appOrder(apps, ["wiki", "brief", "radar"], worked))).toEqual([
    "wiki",
    "brief",
    "radar",
  ]);
  expect(ids(appOrder(apps, ["brief"], worked))).toEqual(["brief", "radar", "wiki"]);
});

test("an unpinned app that worked more recently stands above one that worked longer ago", () => {
  const apps = [app("brief", "Brief"), app("radar", "Radar"), app("wiki", "Wiki")];
  expect(ids(appOrder(apps, [], worked))).toEqual(["radar", "wiki", "brief"]);
});

test("an app that has never worked lands under the ones that have, by name", () => {
  const apps = [app("zed", "Zed"), app("radar", "Radar"), app("apollo", "Apollo")];
  expect(ids(appOrder(apps, [], worked))).toEqual(["radar", "apollo", "zed"]);
});

test("apps sharing a moment keep the order they arrived in", () => {
  const apps = [app("zed", "Zed"), app("apollo", "Apollo"), app("brief", "Brief")];
  expect(ids(appOrder(apps, [], () => 500))).toEqual(["zed", "apollo", "brief"]);
});

test("a pin no live app answers draws nothing", () => {
  const apps = [app("radar", "Radar")];
  expect(ids(appOrder(apps, ["removed", "radar"], worked))).toEqual(["radar"]);
});

test("the collapsed run is the same length whatever the workspace is doing", () => {
  const many = "abcdefghij".split("").map((id) => app(id, id.toUpperCase()));

  const idle = appRun(many, () => false);
  expect(ids(idle.shown)).toEqual(["a", "b", "c", "d", "e", "f", "g", "h"]);
  expect(ids(idle.more)).toEqual(["i", "j"]);

  const busy = appRun(many, (agentId) => agentId === "i");
  expect(ids(busy.shown)).toEqual(["i", "a", "b", "c", "d", "e", "f", "g"]);
  expect(ids(busy.more)).toEqual(["h", "j"]);
});

test("the tail behind More holds every app the run did not, however many there are", () => {
  const many = Array.from({ length: 30 }, (_, at) => app("a" + at, "A" + at));
  const run = appRun(many, () => false);
  expect(run.shown.length).toBe(8);
  expect(run.more.length).toBe(22);
  expect(ids(run.shown.concat(run.more))).toEqual(ids(many));
});

test("a working app rises to the top and leaves the order under it alone", () => {
  const apps = [app("brief", "Brief"), app("radar", "Radar"), app("wiki", "Wiki")];

  const run = appRun(apps, (agentId) => agentId === "radar");
  expect(ids(run.shown)).toEqual(["radar", "brief", "wiki"]);
  expect(ids(run.more)).toEqual([]);

  const rested = appRun(apps, () => false);
  expect(ids(rested.shown)).toEqual(["brief", "radar", "wiki"]);
});

test("pinning moves an app up the order and draws the same number of rows", () => {
  const many = "abcdefghij".split("").map((id) => app(id, id.toUpperCase()));
  const unpinned = appRun(appOrder(many, [], () => null), () => false);
  expect(ids(unpinned.shown)).toEqual(["a", "b", "c", "d", "e", "f", "g", "h"]);

  const pinned = appRun(appOrder(many, ["i"], () => null), () => false);
  expect(ids(pinned.shown)).toEqual(["i", "a", "b", "c", "d", "e", "f", "g"]);
  expect(pinned.more.length).toBe(2);
});

test("the apps list opens collapsed, and the expansion the member asked for holds", () => {
  expect(heldAppsExpanded()).toBe(false);

  holdAppsExpanded(true);
  expect(heldAppsExpanded()).toBe(true);

  holdAppsExpanded(false);
  expect(heldAppsExpanded()).toBe(false);
});

test("the store opens on the expansion this browser holds, and a pick writes it back", () => {
  localStorage.setItem("apps-expanded", "expanded");
  expect(railState().appsExpanded).toBe(true);

  pickAppsExpanded(false);
  expect(railState().appsExpanded).toBe(false);
  expect(heldAppsExpanded()).toBe(false);

  resetRailStore();
  expect(railState().appsExpanded).toBe(false);
});

test("the recency rail heads its own conversations with nothing at all", async () => {
  const today = { ...CHAT_ROW, last_at: stampIso(new Date()) };
  const older = {
    ...CHAT_ROW,
    conversation_id: SECOND_ID,
    title: "Last month's thread",
    last_at: stampIso(new Date(Date.now() - 40 * 24 * 3_600_000)),
  };
  wire({ ...chatsOnWire([today, older]) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByRole("button", { name: /Pick one thread/ })).toBeTruthy();
  expect(screen.getByRole("button", { name: /Last month's thread/ })).toBeTruthy();
  const sidebar = screen.getByRole("navigation", { name: "Workspace" });
  expect(within(sidebar).getAllByRole("heading").map((head) => head.textContent)).toEqual(["Chats"]);
});

test("a group heading shuts its rows, and the browser holds that past a remount", async () => {
  const colleague = {
    ...CHAT_ROW,
    conversation_id: SECOND_ID,
    title: "Colleague thread",
    last_at: stampIso(new Date()),
    mine: false,
    speaker: "sam@example.com",
  };
  wire({ ...chatsOnWire([CHAT_ROW, colleague]) });
  const first = render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await openGroup("Other members");
  expect(await screen.findByRole("button", { name: /Colleague thread/ })).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Other members", expanded: true }));
  expect(screen.queryByRole("button", { name: /Colleague thread/ })).toBeNull();

  first.unmount();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  expect(await screen.findByRole("button", { name: "Other members", expanded: false })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Colleague thread/ })).toBeNull();
});

test("opening the group at the foot leaves the run above it standing", async () => {
  const today = { ...CHAT_ROW, last_at: stampIso(new Date()) };
  const colleague = {
    ...today,
    conversation_id: SECOND_ID,
    title: "Colleague thread",
    mine: false,
    speaker: "pat@example.com",
  };
  wire({ ...chatsOnWire([today, colleague]) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByRole("button", { name: /Pick one thread/ })).toBeTruthy();
  await openGroup("Other members");
  expect(await screen.findByRole("button", { name: /Colleague thread/ })).toBeTruthy();
  expect(screen.getByRole("button", { name: /Pick one thread/ })).toBeTruthy();
  expect(heldRailShut()).toEqual([]);
});

test("the query holding the narrow rail's groups open is the theme's own breakpoint", () => {
  const theme = readFileSync(join(import.meta.dirname, "..", "..", "src", "theme.css"), "utf8");
  const declared = /--breakpoint-narrow:\s*(\d+)px/.exec(theme);
  expect(declared).not.toBeNull();
  expect(NARROW).toBe("(width < " + declared![1] + "px)");
});

test("merging keeps held rows the fetch does not know and prefers fetched rows it does", () => {
  const held = [{ ...row("a", hoursAgo(3)), title: "held copy" }, row("b", hoursAgo(2))];
  const fetched = [{ ...row("a", hoursAgo(2.5)), title: "fetched copy" }];
  const merged = mergeChats(fetched, held);
  expect(merged.map((entry) => entry.conversation_id)).toEqual(["b", "a"]);
  expect(merged[1].title).toBe("fetched copy");
});

test("a deep link waits while the rail loads instead of denying the conversation", async () => {
  location.hash = "#/c/" + CONVO_ID;
  wire({
    "/objects/conversation$": () =>
      new Promise<Response>(() => {
        return;
      }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("Loading…")).toBeTruthy();
  expect(screen.queryByText(NOT_SHARED)).toBeNull();
});

test("the loading rail draws placeholder rows shaped like chat rows, not the word", async () => {
  wire({
    "/objects/conversation$": () =>
      new Promise<Response>(() => {
        return;
      }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const waiting = await screen.findByRole("status", { name: "Loading chats" });
  const rows = within(waiting).getAllByRole("listitem");
  expect(rows.length).toBeGreaterThan(1);
  for (const row of rows) {
    expect(row.querySelectorAll("[data-part=skeleton]")).toHaveLength(2);
  }
});

test("the rail drops its placeholder rows once the chats land", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByText(CHAT_ROW.title);
  expect(screen.queryByRole("status", { name: "Loading chats" })).toBeNull();
});

test("a rail read that fails states so and keeps the rows it has", async () => {
  wire({ "/objects/conversation$": () => new Response("nope", { status: 503 }) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByText("Conversations did not refresh.");
  const toast = document.querySelector("[data-slot=toast]") as HTMLElement;
  expect(toast.textContent).toContain("Conversations did not refresh.");
  expect(toast.textContent).toContain("Error 503 — reload to retry.");
});

test("a rail read the session refuses states nothing, since sign-in answers it", async () => {
  wire({ "/objects/conversation$": () => new Response("unauthorized", { status: 401 }) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await waitFor(() => expect(screen.queryByText("Loading…")).toBeNull());
  expect(document.querySelector("[data-slot=toast]")).toBeNull();
});

test("a new-conversation link naming no agent of this workspace says so", async () => {
  location.hash = "#/new/99999999-9999-4999-8999-999999999999";
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("No such app.")).toBeTruthy();
});

test("a first message opens a conversation, lands it in the rail, and routes to it", async () => {
  const posts: string[] = [];
  wire({
    "/chat": (url) => {
      posts.push(url);
      return json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello there" });
    },
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.type(screen.getByLabelText("Ask UFO"), "hello there");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  await waitFor(() => expect(location.hash).toBe("#/c/" + CONVO_ID));
  expect(posts[0]).toContain("?conversation=new");
  expect(screen.getByRole("button", { name: "Visibility: Private" })).toBeTruthy();
  const railRow = await screen.findByRole("button", { name: /hello there/ });
  expect(railRow.getAttribute("aria-current")).toBe("true");
  expect(within(screen.getByTestId("log")).getByText("hello there")).toBeTruthy();
});

test("a second message sent before the first is answered opens no second conversation", async () => {
  const posts: string[] = [];
  let found: (payload: unknown) => void = () => {};
  const founding = new Promise<Response>((resolve) => {
    found = (payload) => resolve(json(payload));
  });
  wire({
    "/chat": (url) => {
      posts.push(url);
      return posts.length === 1
        ? founding
        : json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "first half" });
    },
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.type(screen.getByLabelText("Ask UFO"), "first half");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  // Until that POST answers nothing knows which conversation it opened, and a second send to the `new`
  // sentinel opens another one, so the composer holds the words rather than founding again.
  await userEvent.type(screen.getByLabelText("Ask UFO"), "second half");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  expect(posts.length).toBe(1);
  expect((screen.getByLabelText("Ask UFO") as HTMLInputElement).value).toBe("second half");

  found({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "first half", opened_run: true });
  await waitFor(() => expect(location.hash).toBe("#/c/" + CONVO_ID));

  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Send" }).hasAttribute("disabled")).toBe(false),
  );
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(posts.length).toBe(2));
  expect(posts[1]).toContain("?conversation=" + CONVO_ID);
  expect(within(screen.getByTestId("log")).getByText("second half")).toBeTruthy();
});

test("two submits the page could not re-render between still open one conversation", async () => {
  const posts: string[] = [];
  wire({
    "/chat": (url) => {
      posts.push(url);
      return json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "one thought" });
    },
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.type(screen.getByLabelText("Ask UFO"), "one thought");
  const form = screen.getByLabelText("Ask UFO").closest("form");

  await act(async () => {
    form?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    form?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });

  expect(posts.length).toBe(1);
  expect(posts[0]).toContain("?conversation=new");
});

test("a first message sent before the rail resolves still lands, and the rail merge keeps it", async () => {
  let releaseRail: ((value: Response) => void) | null = null;
  wire({
    "/objects/conversation$": () =>
      new Promise<Response>((resolve) => {
        releaseRail = resolve;
      }),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "early words" }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.type(screen.getByLabelText("Ask UFO"), "early words");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(location.hash).toBe("#/c/" + CONVO_ID));
  expect(within(screen.getByTestId("log")).getByText("early words")).toBeTruthy();
  expect(screen.queryByText(NOT_SHARED)).toBeNull();

  releaseRail!(
    json({
      objects: [
        conversationObject({
          ...CHAT_ROW,
          conversation_id: "88888888-8888-4888-8888-888888888888",
          last_at: stampIso(new Date()),
        }),
      ],
    }),
  );
  expect(await screen.findByRole("button", { name: /Pick one thread/ })).toBeTruthy();
  expect(screen.getByRole("button", { name: /early words/ })).toBeTruthy();
  expect(within(screen.getByTestId("log")).getByText("early words")).toBeTruthy();
});

test("a running thread's row draws the live dot and an idle one's rests in the same column", async () => {
  const running = { ...CHAT_ROW, turn: "running" as const };
  const idle = {
    ...CHAT_ROW,
    conversation_id: "66666666-6666-4666-8666-666666666666",
    title: "Answered thread",
    turn: "idle" as const,
  };
  wire(chatsOnWire([running, idle]));
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const live = await screen.findByRole("button", { name: /Pick one thread/ });
  const rest = screen.getByRole("button", { name: /Answered thread/ });
  const column = (button: HTMLElement) => button.querySelector("[data-turn]")!;
  expect(column(live).className).toBe(column(rest).className);
  expect(column(live).className).toContain("size-(--size-glyph)");
  expect(column(live).firstElementChild!.getAttribute("class")).toContain("animate-spin");
  expect(column(rest).firstElementChild!.className).toContain("text-ink-quiet");
  expect(column(rest).firstElementChild!.className).not.toContain("animate-spin");
});

test("a row's dot follows the listing, not the row it was founded with", async () => {
  const other = "66666666-6666-4666-8666-666666666666";
  let held: RailTurn[] = ["running", "idle"];
  vi.useFakeTimers();
  wire({
    "/objects/conversation$": () =>
      json({
        objects: [
          { ...CHAT_ROW, turn: held[0] },
          { ...CHAT_ROW, conversation_id: other, title: "Answered thread", turn: held[1] },
        ].map(conversationObject),
      }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await settle(0);

  const mark = (title: string) =>
    screen
      .getByRole("button", { name: new RegExp(title) })
      .querySelector("[data-turn]")!
      .firstElementChild!.getAttribute("class")!;
  expect(mark("Pick one thread")).toContain("animate-spin");
  expect(mark("Answered thread")).toContain("text-ink-quiet");

  held = ["idle", "running"];
  await settle(WORKING_STATUS_MS);

  expect(mark("Pick one thread")).toContain("text-ink-quiet");
  expect(mark("Answered thread")).toContain("animate-spin");
  vi.useRealTimers();
});

test("a parked turn is not work in flight, so the rail waits out the resting cadence", async () => {
  vi.useFakeTimers();
  const { calls } = wire({
    "/objects/conversation$": () =>
      json({ objects: [conversationObject({ ...CHAT_ROW, turn: "parked" })] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await settle(0);
  const listings = () => calls.filter((url) => url.includes("/objects/conversation")).length;
  const walked = listings();

  await settle(WORKING_STATUS_MS);
  expect(listings()).toBe(walked);

  await settle(RESTING_STATUS_MS - WORKING_STATUS_MS);
  expect(listings()).toBe(walked + 1);
  vi.useRealTimers();
});

test("a running turn the rail does not draw earns the working cadence only once shown", async () => {
  vi.useFakeTimers();
  holdRailShown(PORTAL_ONLY);
  const slack = { ...CHAT_ROW, surface: "slack", surface_label: "DM", turn: "running" as const };
  const { calls } = wire({
    "/objects/conversation$": () => json({ objects: [conversationObject(slack)] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await settle(0);
  const listings = () => calls.filter((url) => url.includes("/objects/conversation")).length;
  const walked = listings();

  await settle(WORKING_STATUS_MS);
  expect(listings()).toBe(walked);

  await act(async () => {
    pickRailShown(EVERY_SURFACE);
  });
  await settle(WORKING_STATUS_MS);
  expect(listings()).toBe(walked + 1);
  vi.useRealTimers();
});

test("a thread holding unread messages draws its mark live, on every surface", async () => {
  const slack = {
    ...CHAT_ROW,
    conversation_id: "66666666-6666-4666-8666-666666666666",
    title: "Answered thread",
    surface: "slack",
    surface_label: "DM",
    unread: true,
  };
  wire(chatsOnWire([{ ...CHAT_ROW, unread: true }, slack]));
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const mark = (title: string) =>
    screen
      .getByRole("button", { name: new RegExp(title) })
      .querySelector("[data-turn]")!
      .firstElementChild!.getAttribute("class")!;
  await screen.findByRole("button", { name: /Pick one thread/ });
  expect(mark("Pick one thread")).toContain("text-live");
  expect(mark("Answered thread")).toContain("text-live");
});

test("opening a thread clears its unread mark", async () => {
  wire({
    ...chatsOnWire([{ ...CHAT_ROW, unread: true }]),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const row = await screen.findByRole("button", { name: /Pick one thread/ });
  expect(row.querySelector("[data-turn]")!.firstElementChild!.getAttribute("class")).toContain(
    "text-live",
  );

  await userEvent.click(row);

  await waitFor(() => {
    const mark = screen
      .getByRole("button", { name: /Pick one thread/ })
      .querySelector("[data-turn]")!.firstElementChild!;
    expect(mark.getAttribute("class")).toContain("text-ink-quiet");
  });
});

test("a rail row opens its conversation's transcript", async () => {
  const { calls } = wire({
    ...chatsOnWire([CHAT_ROW]),
    "/transcript": () => json({ messages: [{ role: "user", text: "earlier words" }] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: /Pick one thread/ }));

  expect(await screen.findByText("earlier words")).toBeTruthy();
  expect(
    calls.some((url) =>
      url.includes("/agents/" + AGENT_ID + "/transcript?conversation=" + CONVO_ID),
    ),
  ).toBe(true);
  expect(location.hash).toBe("#/c/" + CONVO_ID);
});

test("sending from an existing conversation posts to it and bumps its rail row", async () => {
  const older = { ...CHAT_ROW, last_at: "2026-08-01T08:00:00" };
  const newer = {
    ...CHAT_ROW,
    conversation_id: "66666666-6666-4666-8666-666666666666",
    title: "The newer thread",
    last_at: "2026-08-01T09:00:00",
  };
  const { calls } = wire({
    ...chatsOnWire([newer, older]),
    "/transcript": () => json({ messages: [] }),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: older.title }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: /Pick one thread/ }));
  await userEvent.type(screen.getByLabelText("Ask UFO"), "follow-up");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  await waitFor(() =>
    expect(calls.some((url) => url.includes("/chat?conversation=" + CONVO_ID))).toBe(true),
  );
  await waitFor(() => {
    const rows = screen.getAllByRole("button", { name: /thread/ });
    expect(rows[0].textContent).toContain("Pick one thread");
  });
});

test("a row of a non-main agent names its agent in the rail", async () => {
  const foreign = {
    ...CHAT_ROW,
    conversation_id: "77777777-7777-4777-8777-777777777777",
    agent_id: SECOND_ID,
    agent_name: "second",
    title: "An ops question",
  };
  wire({ ...chatsOnWire([foreign]) });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  const railRow = await screen.findByRole("button", { name: /An ops question/ });
  expect(rowWords(railRow)).toBe("An ops question");
  fireEvent.focus(railRow);
  expect((await hoverCard()).textContent).toContain("Second");
});

test("a row from another surface draws its glyph and states the surface's own name", async () => {
  const slack = {
    ...CHAT_ROW,
    surface: "slack",
    surface_label: "DM",
    title: "Slack question",
  };
  holdRailShown(EVERY_SURFACE);
  wire({ ...chatsOnWire([slack]) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const railRow = await screen.findByRole("button", { name: /Slack question/ });
  expect(rowWords(railRow)).toBe("Slack question");
  expect(railRow.querySelector(".tabler-icon-brand-slack")).not.toBeNull();
  fireEvent.focus(railRow);
  expect((await hoverCard()).textContent).toContain("DM");
});

test("a cli row draws the terminal glyph and reads as Terminal, never as the surface's own name", async () => {
  const cli = { ...CHAT_ROW, surface: "ufo", surface_label: null, title: "Deploy the branch" };
  holdRailShown(EVERY_SURFACE);
  wire({ ...chatsOnWire([cli]) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const railRow = await screen.findByRole("button", { name: /Deploy the branch/ });
  expect(railRow.querySelector(".tabler-icon-terminal-2")).not.toBeNull();
  fireEvent.focus(railRow);
  expect((await hoverCard()).textContent).toContain("Terminal");
});

test("the surface glyph is drawn at the sidebar's glyph size, not at the row's text size", async () => {
  const slack = { ...CHAT_ROW, surface: "slack", surface_label: "#ops", title: "Slack question" };
  holdRailShown(EVERY_SURFACE);
  wire({ ...chatsOnWire([slack]) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const railRow = await screen.findByRole("button", { name: /Slack question/ });
  const drawn = railRow
    .querySelector("[data-turn] .tabler-icon-brand-slack")!
    .getAttribute("class")!
    .split(" ");
  expect(drawn).toContain("size-3.5");
});

test("hovering a row opens the card on its title, opening words and metadata", async () => {
  const slack = { ...CHAT_ROW, surface: "slack", surface_label: "#ops" };
  holdRailShown(EVERY_SURFACE);
  wire({ ...chatsOnWire([slack]) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const railRow = await screen.findByRole("button", { name: /Pick one thread/ });
  await userEvent.hover(railRow);

  const card = await hoverCard();
  expect(card.textContent).toContain("Pick one thread");
  expect(card.textContent).toContain("Pick one thread to carry the work.");
  expect(card.textContent).toContain("#ops");
  expect(card.textContent).toContain(rowMoment(slack.last_at, new Date()));
  expect(railRow.getAttribute("aria-describedby")).toBe(card.getAttribute("id"));
});

test("a row reached by the keyboard opens the same card", async () => {
  wire({ ...chatsOnWire([CHAT_ROW]) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const railRow = await screen.findByRole("button", { name: /Pick one thread/ });
  railRow.focus();

  expect((await hoverCard()).textContent).toContain("Pick one thread to carry the work.");
  expect(railRow.closest("li")!.getAttribute("data-state")).toBe("open");
});

test("a conversation another member spoke stands at the foot and names them", async () => {
  const colleague = {
    ...CHAT_ROW,
    conversation_id: "55555555-5555-4555-8555-555555555555",
    title: "The deploy thread",
    mine: false,
    speaker: "Pat Reyes (pat@example.com)",
  };
  wire({ ...chatsOnWire([CHAT_ROW, colleague]) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await openGroup("Other members");
  const railRow = await screen.findByRole("button", { name: /The deploy thread/ });
  expect(rowWords(railRow)).toBe("The deploy thread");
  fireEvent.focus(railRow);
  expect((await hoverCard()).textContent).toContain("Pat Reyes");
  const section = railRow.closest("section");
  expect(section?.textContent).toContain("Other members");
  expect(section?.textContent).not.toContain("Pick one thread");
  expect(screen.getByRole("button", { name: /Pick one thread/ }).closest("section")).toBeNull();
});

/** jsdom lays nothing out, so the frame and the words it holds are given their widths rather than measured. */
function overrunning(row: HTMLElement, frameWidth: number, textWidth: number) {
  const frame = row.querySelector(":scope > span:not([data-turn])") as HTMLElement;
  const text = frame.querySelector("span") as HTMLElement;
  Object.defineProperty(frame, "clientWidth", { value: frameWidth, configurable: true });
  text.getBoundingClientRect = () => ({ width: textWidth }) as DOMRect;
  return { frame, text };
}

test("a title too long for its row travels its overrun under the pointer, and rests again after", async () => {
  const long = { ...CHAT_ROW, title: "The thread about the migration we ran on the release branch" };
  wire({ ...chatsOnWire([long]) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const row = await screen.findByRole("button", { name: /The thread about the migration/ });
  const rail = row.closest("li") as HTMLElement;
  const { frame, text } = overrunning(row, 180, 300);
  expect(text.style.transform).toBe("translateX(0px)");
  expect(text.className).toContain("inline");
  expect(text.className).not.toContain("inline-block");

  fireEvent.pointerEnter(rail);
  await waitFor(() => expect(text.style.transform).toBe("translateX(-136px)"));
  /* Travelled at a pace rather than in a duration: 136px at 45px a second. */
  expect(text.style.transitionDuration).toBe("3022ms");
  expect(text.className).toContain("inline-block");
  expect(frame.className).toContain("line-fade-x");

  fireEvent.pointerLeave(rail);
  expect(text.style.transform).toBe("translateX(0px)");
  expect(text.style.transitionDuration).toBe("150ms");
  expect(frame.className).toContain("line-fade-e");
  expect(text.className).not.toContain("inline-block");
});

test("a title the row holds whole moves nothing, and the keyboard starts one that does not", async () => {
  wire({ ...chatsOnWire([CHAT_ROW]) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const row = await screen.findByRole("button", { name: /Pick one thread/ });
  const { frame, text } = overrunning(row, 180, 180);
  fireEvent.pointerEnter(row.closest("li") as HTMLElement);
  expect(text.style.transform).toBe("translateX(0px)");
  expect(frame.className).not.toContain("line-fade");

  overrunning(row, 180, 240);
  fireEvent.focus(row);
  await waitFor(() => expect(text.style.transform).toBe("translateX(-76px)"));
  fireEvent.blur(row);
  expect(text.style.transform).toBe("translateX(0px)");
});

test("an origin rail row opens a live comment chat", async () => {
  const slack = {
    ...CHAT_ROW,
    surface: "slack",
    surface_label: "DM",
    title: "Slack question",
  };
  const linked = {
    id: CONVO_ID,
    surface: "slack",
    surface_label: "DM",
    audience: "member:0a1b2c3d-0000-4000-8000-000000000009",
    member_email: MEMBER.email,
    description: "",
    speakers: [],
    turn_count: 1,
    created_at: "2026-07-30T10:00:00",
    last_turn_at: "2026-07-30T11:00:00",
    readable: true,
    disclosable: false,
    commentable: true,
    agent: { id: AGENT.id, name: AGENT.name },
  };
  holdRailShown(EVERY_SURFACE);
  const { calls } = wire({
    ...chatsOnWire([slack]),
    "/api/chats": () => json({ chats: [slack], conversation: linked }),
    "/transcript": () => json({ messages: [{ role: "user", text: "slack words" }] }),
    "/chat": () =>
      json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "Slack question" }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: /Slack question/ }));

  expect(await screen.findByText("slack words")).toBeTruthy();
  expect(screen.getByLabelText("Ask UFO")).toBeTruthy();
  expect(screen.queryByText(/read-only here/)).toBeNull();

  await userEvent.type(screen.getByLabelText("Ask UFO"), "from the portal");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() =>
    expect(calls.some((url) => url.includes("/chat?conversation=" + CONVO_ID))).toBe(true),
  );
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("comment", {
    id: SECOND_ID,
    text: "You commented from the portal.",
  });
  expect(screen.queryByText("You commented from the portal.")).toBeNull();
});

test("a private extension conversation opens the live chat", async () => {
  const sweep = {
    ...CHAT_ROW,
    agent_id: "22222222-2222-4222-8222-222222222222",
    agent_name: "Daily-Brief",
    surface: "extension:sweep",
    surface_label: null,
    title: "Daily brief",
  };
  wire({
    ...chatsOnWire([sweep]),
    "/transcript": () => json({ messages: [{ role: "assistant", text: "Work to finish" }] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: /Daily brief/ }));

  expect(await screen.findByText("Work to finish")).toBeTruthy();
  const crumb = within(screen.getByRole("navigation", { name: "Breadcrumb" }));
  expect(crumb.getByText("Daily brief")).toBeTruthy();
  expect(crumb.getByText("Daily-Brief")).toBeTruthy();
  expect(crumb.queryByRole("link")).toBeNull();
  expect(screen.getByLabelText("Ask UFO")).toBeTruthy();
  expect(screen.queryByText(/read-only here/)).toBeNull();
});

test("a conversation no read of this account's answers is named unshared, not missing", async () => {
  location.hash = "#/c/99999999-9999-4999-8999-999999999999";
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText(NOT_SHARED)).toBeTruthy();
});

test("a permalink read refused for the session offers sign-in rather than a denial", async () => {
  location.hash = "#/c/" + CONVO_ID;
  wire({
    "/api/chats": (url) =>
      url.includes("conversation=")
        ? new Response("missing or unknown session cookie", { status: 401 })
        : json({ chats: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("Session ended")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Sign in" }).getAttribute("href")).toBe("/login");
  expect(screen.queryByText(NOT_SHARED)).toBeNull();
});

test("a permalink read the server faults on states the fault rather than a denial", async () => {
  location.hash = "#/c/" + CONVO_ID;
  wire({
    "/api/chats": (url) =>
      url.includes("conversation=") ? new Response("boom", { status: 500 }) : json({ chats: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("Error 500 — reload to retry.")).toBeTruthy();
  expect(screen.queryByText(NOT_SHARED)).toBeNull();
});

function slackConversation(fields: Record<string, unknown> = {}) {
  return {
    id: CONVO_ID,
    agent: { id: AGENT_ID, name: AGENT.name },
    surface: "slack",
    surface_label: "#ops-warehouse",
    audience: "shared",
    member_email: null,
    description: "Warehouse restock plan",
    source: "https://acme.slack.com/archives/C1/p1700000000000100",
    turn_count: 1,
    created_at: "2026-08-01T08:00:00Z",
    last_turn_at: "2026-08-01T08:01:00Z",
    readable: true,
    disclosable: false,
    commentable: true,
    ...fields,
  };
}

test("a Slack conversation permalink opens a live comment chat", async () => {
  location.hash = "#/c/" + CONVO_ID;
  wire({
    "/api/chats": (url) =>
      url.includes("conversation=")
        ? json({ chats: [], conversation: slackConversation() })
        : json({ chats: [] }),
    "/transcript": () =>
      json({
        messages: [
          { role: "user", text: "from Slack" },
          { role: "assistant", text: "reply in Slack" },
        ],
      }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("from Slack")).toBeTruthy();
  expect(screen.getByText("reply in Slack")).toBeTruthy();
  expect(screen.getByLabelText("Ask UFO")).toBeTruthy();
  expect(screen.queryByText(/read-only here/)).toBeNull();
  expect(screen.getByText("from Slack").closest("main")).not.toBeNull();
  expect(
    within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByText(
      "Warehouse restock plan",
    ),
  ).toBeTruthy();
});

test("a Slack conversation is headed like a web thread, marked with its way out to Slack", async () => {
  location.hash = "#/c/" + CONVO_ID;
  wire({
    "/api/chats": (url) =>
      url.includes("conversation=")
        ? json({ chats: [], conversation: slackConversation() })
        : json({ chats: [] }),
    "/transcript": () =>
      json({ messages: [{ role: "user", text: "from Slack" }] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByText("from Slack");
  const header = within(screen.getByRole("main"));
  const crumb = within(screen.getByRole("navigation", { name: "Breadcrumb" }));
  expect(crumb.getByText("Warehouse restock plan")).toBeTruthy();
  expect(crumb.getByText(agentName(AGENT.name))).toBeTruthy();
  expect(crumb.queryByText(AGENT.model)).toBeNull();
  expect(header.queryByRole("heading", { name: "Warehouse restock plan" })).toBeNull();
  expect(header.queryByRole("button", { name: "All conversations" })).toBeNull();

  const out = header.getByRole("link", { name: "Open #ops-warehouse in Slack" });
  expect(out.getAttribute("href")).toBe("https://acme.slack.com/archives/C1/p1700000000000100");
  expect(out.getAttribute("target")).toBe("_blank");
  expect(out.textContent).toContain("#ops-warehouse");
  expect(within(out).getByText("↗").className).toContain("text-ink-soft");
  const mark = out.querySelector("svg");
  expect(mark?.getAttribute("class")).toContain("size-(--size-surface-mark)");

  expect(header.queryByRole("button", { name: "Back to Agents" })).toBeNull();
});

test("a terminal conversation is marked with its surface and no way out", async () => {
  location.hash = "#/c/" + CONVO_ID;
  const terminal = slackConversation({
    surface: "ufo",
    surface_label: null,
    source: null,
    description: "Deploy the branch",
  });
  wire({
    "/api/chats": (url) =>
      url.includes("conversation=")
        ? json({ chats: [], conversation: terminal })
        : json({ chats: [] }),
    "/transcript": () =>
      json({ messages: [{ role: "user", text: "from the CLI" }] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByText("from the CLI");
  const header = within(screen.getByRole("main"));
  expect(header.getByText("Deploy the branch")).toBeTruthy();
  expect(header.getByText("Terminal")).toBeTruthy();
  expect(header.getByText("Terminal").closest("a")).toBeNull();
  expect(screen.getByLabelText("Ask UFO")).toBeTruthy();
  expect(screen.queryByText(/read-only here/)).toBeNull();
});

test("a markdown file in a Slack conversation opens the attachment sheet", async () => {
  location.hash = "#/c/" + CONVO_ID;
  wire({
    ["/conversations/" + CONVO_ID + "/slots/artifacts"]: () =>
      json({
        type: "artifacts",
        artifacts: [
          {
            filename: "notes.md",
            subject: null,
            media_type: "text/markdown",
            size_bytes: 512,
            created_at: "2026-08-16T12:00:00Z",
            url: "/dl/notes.md",
            preview: null,
          },
        ],
        truncated: false,
      }),
    "/transcript": () =>
      json({
        messages: [
          { role: "user", text: "from Slack" },
          {
            role: "assistant",
            text: "wrote it up",
            files: [
              {
                filename: "notes.md",
                url: "/dl/notes.md",
                size_bytes: 512,
                preview_url: null,
                media_type: "text/markdown",
              },
            ],
          },
        ],
      }),
    "/dl/notes.md": () => new Response("# Notes"),
    "/slots": () =>
      json({
        slots: [
          { id: "artifacts", label: "Artifacts", icon: "artifact", kind: "artifacts", count: 1 },
        ],
      }),
    "/api/chats": (url) =>
      url.includes("conversation=")
        ? json({
            chats: [],
            conversation: {
              id: CONVO_ID,
              agent: { id: AGENT_ID, name: AGENT.name },
              surface: "slack",
              surface_label: "#ops-warehouse",
              audience: "shared",
              member_email: null,
              source: "https://acme.slack.com/archives/C1/p1700000000000100",
              turn_count: 1,
              created_at: "2026-08-01T08:00:00Z",
              last_turn_at: "2026-08-01T08:01:00Z",
              readable: true,
              disclosable: false,
              commentable: true,
            },
          })
        : json({ chats: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const card = await screen.findByRole("button", { name: "notes.md" });
  expect(screen.queryByRole("link", { name: "notes.md" })).toBeNull();
  await userEvent.click(card);
  expect(location.hash).toBe("#/c/" + CONVO_ID);
  const sheet = await screen.findByRole("dialog", { name: "notes.md" });
  expect(await within(sheet).findByRole("heading", { name: "Notes" })).toBeTruthy();
  await userEvent.click(within(sheet).getByRole("button", { name: "Close" }));
  expect(location.hash).toBe("#/c/" + CONVO_ID);
});

test("a rail row lands in the chat app when one is shipped", async () => {
  location.hash = "#/";
  wire({ ...chatsOnWire([CHAT_ROW]) });
  render(<App agents={[AGENT, CHAT_APP]} member={MEMBER} onAgents={() => {}} />);

  const rail = within(screen.getByRole("navigation", { name: "Workspace" }));
  await userEvent.click(await rail.findByRole("button", { name: /Pick one thread/ }));

  expect(location.hash).toBe("#/agents/" + CHAT_APP_ID + "?open=" + CONVO_ID);
});

test("the ask row opens the chat app at its start screen when one is shipped", async () => {
  location.hash = "#/";
  wire({});
  render(<App agents={[AGENT, CHAT_APP]} member={MEMBER} onAgents={() => {}} />);

  const rail = within(screen.getByRole("navigation", { name: "Workspace" }));
  await userEvent.click(rail.getAllByRole("button", { name: "New chat" })[0]);

  expect(location.hash).toBe("#/agents/" + CHAT_APP_ID + "?open=compose");
});

test("the chord the row prints starts the conversation the row would", async () => {
  location.hash = "#/";
  wire({});
  render(<App agents={[AGENT, CHAT_APP]} member={MEMBER} onAgents={() => {}} />);

  const rail = within(screen.getByRole("navigation", { name: "Workspace" }));
  const row = rail.getAllByRole("button", { name: "New chat" })[0];
  expect(row.getAttribute("aria-keyshortcuts")).toBe("Meta+Shift+O");

  await userEvent.keyboard("{Meta>}{Shift>}O{/Shift}{/Meta}");

  expect(location.hash).toBe("#/agents/" + CHAT_APP_ID + "?open=compose");
});

test("the ask control targets the main agent, and offers no other", async () => {
  wire({});
  const single = render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await userEvent.click(screen.getAllByRole("button", { name: "New chat" })[0]);
  expect(location.hash).toBe("#/new/" + AGENT_ID);
  single.unmount();

  location.hash = "";
  wire({});
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);
  await userEvent.click(screen.getAllByRole("button", { name: "New chat" })[0]);

  expect(location.hash).toBe("#/new/" + AGENT_ID);
  expect(await screen.findByLabelText("Ask UFO")).toBeTruthy();
  expect(screen.queryByRole("navigation", { name: "Breadcrumb" })).toBeNull();
});

test("the conversations rail stands in the sidebar whatever the destination", async () => {
  wire({
    ...chatsOnWire([CHAT_ROW]),
    "/transcript": () => json({ messages: [] }),
    "/workspace/artifacts": () => json({ artifacts: [] }),
    "/objects/site": () => objectIndex(SITE_KIND, []),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const rail = within(screen.getByRole("navigation", { name: "Workspace" }));
  expect(await rail.findByRole("button", { name: /Pick one thread/ })).toBeTruthy();
  expect(rail.getAllByRole("button", { name: "New chat" })[0]).toBeTruthy();

  await userEvent.click(rail.getByRole("button", { name: "Settings" }));
  expect(await screen.findByRole("heading", { name: "Team" })).toBeTruthy();
  expect(rail.getByRole("button", { name: /Pick one thread/ })).toBeTruthy();
  expect(rail.getAllByRole("button", { name: "New chat" })[0]).toBeTruthy();
});

test("the chat header names the agent holding the conversation and what it is called, never the model", async () => {
  wire({
    ...chatsOnWire([CHAT_ROW]),
    "/transcript": () => json({ messages: [] }),
    "/settings": () => new Response("nope", { status: 503 }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: /Pick one thread/ }));
  const crumb = within(screen.getByRole("navigation", { name: "Breadcrumb" }));
  expect(crumb.getByText("Pick one thread")).toBeTruthy();
  expect(crumb.getByText("Assistant")).toBeTruthy();
  expect(crumb.queryByText(AGENT.model)).toBeNull();

  await userEvent.click(crumb.getByRole("link", { name: "Back to Assistant" }));
  expect(location.hash).toBe("#/new/" + AGENT_ID);
});

test("a deep link is not blamed while the rail is the thing that failed", async () => {
  location.hash = "#/c/" + CONVO_ID;
  wire({ "/objects/conversation$": () => new Response("nope", { status: 500 }) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const notes = await screen.findAllByText("Couldn't load conversations.");
  expect(notes.length).toBe(2);
  expect(screen.queryByText(NOT_SHARED)).toBeNull();
});

test("a failed send neither bumps the rail nor reorders it", async () => {
  const older = { ...CHAT_ROW, last_at: hoursAgo(3) };
  const newer = {
    ...CHAT_ROW,
    conversation_id: "66666666-6666-4666-8666-666666666666",
    title: "The newer thread",
    last_at: hoursAgo(2),
  };
  wire({
    ...chatsOnWire([newer, older]),
    "/transcript": () => json({ messages: [] }),
    "/chat": () => new Response("nope", { status: 500 }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: /Pick one thread/ }));
  await userEvent.type(screen.getByLabelText("Ask UFO"), "doomed");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  expect(await screen.findByText("Error 500 — try again.")).toBeTruthy();
  const rows = screen.getAllByRole("button", { name: /thread/ });
  expect(rows[0].textContent).toContain("The newer thread");
});

test("a linked conversation past the rail's bound resolves by id", async () => {
  location.hash = "#/c/" + CONVO_ID;
  wire({
    "/api/chats": (url) =>
      url.includes("conversation=") ? json({ chats: [CHAT_ROW] }) : json({ chats: [] }),
    "/transcript": () => json({ messages: [{ role: "user", text: "linked words" }] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("linked words")).toBeTruthy();
  expect(await screen.findByRole("button", { name: /Pick one thread/ })).toBeTruthy();
});

test("a hash naming an agent this member cannot reach reports it", async () => {
  location.hash = "#/agents/99999999-9999-4999-8999-999999999999";
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("No such app.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /^Menu for/ })).toBeNull();
});

test("the sidebar marks the destination the member is in and leaves the others off", async () => {
  location.hash = "#/";
  wire({
    "/workspace/team": () => json({ members: [], can_manage: false, domain: null }),
    "/workspace/radar": () => json({ runs: [] }),
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, []),
    "/objects/source_trigger": () => objectIndex(TRIGGER_KIND, []),
    "/workspace/memory": () => json({ available: true, kinds: [], matches: [] }),
    "/skills": () => json({ skills: [] }),
    "/objects/site": () => objectIndex(SITE_KIND, []),
    "/workspace/artifacts": () => json({ artifacts: [] }),
    "/connections": () => json({ connections: [] }),
    "/workspace/first-run": () => json({ providers: [], mcp_servers: [], connectors: [] }),
    "/github/coverage": () => json({ api: false, sources: false }),
    "/settings": () => new Response("nope", { status: 503 }),
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  const rail = within(screen.getByRole("navigation", { name: "Workspace" }));
  const marked = () =>
    ["New chat", "Automations", "Connections", "Settings"].filter(
      (name) => rail.getAllByRole("button", { name })[0].getAttribute("aria-current") === "true",
    );

  expect(marked()).toEqual(["New chat"]);

  await userEvent.click(rail.getByRole("button", { name: "Settings" }));
  await waitFor(() => expect(destination()).toBe("Team"));
  expect(marked()).toEqual(["Settings"]);

  await userEvent.click(rail.getByRole("button", { name: "Automations" }));
  await waitFor(() => expect(marked()).toEqual(["Automations"]));

  await userEvent.click(rail.getByRole("button", { name: "Connections" }));
  await waitFor(() => expect(marked()).toEqual(["Connections"]));
});

test("a failed rail read states it and retries on demand", async () => {
  let failures = 0;
  wire({
    "/objects/conversation$": () => {
      failures += 1;
      return failures === 1
        ? new Response("nope", { status: 500 })
        : json({ objects: [conversationObject(CHAT_ROW)] });
    },
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("Couldn't load conversations.")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByRole("button", { name: /Pick one thread/ })).toBeTruthy();
});

test("a browser holding no filter admits every surface, and holds what a member names", () => {
  expect(heldRailShown()).toEqual(EVERY_SURFACE);

  holdRailShown({ terminal: true, slack: false, imessage: false });
  expect(heldRailShown()).toEqual({ terminal: true, slack: false, imessage: false });

  holdRailShown(EVERY_SURFACE);
  expect(heldRailShown()).toEqual(EVERY_SURFACE);

  holdRailShown(PORTAL_ONLY);
  expect(heldRailShown()).toEqual(PORTAL_ONLY);
});









test("a founded row spins while its turn runs and rests when the turn ends", async () => {
  wire({
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello there" }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.type(screen.getByLabelText("Ask UFO"), "hello there");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  const turnOf = () =>
    screen.getByRole("button", { name: /hello there/ }).querySelector("[data-turn]")!;
  await waitFor(() => expect(turnOf().getAttribute("data-turn")).toBe("running"));
  expect(turnOf().firstElementChild!.getAttribute("class")).toContain("animate-spin");

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("terminal", { status: "done", model: "opus", tokens: 12 });

  await waitFor(() => expect(turnOf().getAttribute("data-turn")).toBe("idle"));
  expect(turnOf().firstElementChild!.getAttribute("class")).not.toContain("animate-spin");
});

test("a parked turn marks its row blocked rather than resting it", async () => {
  wire({
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello there" }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.type(await screen.findByLabelText("Ask UFO"), "hello there");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  const turnOf = () =>
    screen.getByRole("button", { name: /hello there/ }).querySelector("[data-turn]")!;
  await waitFor(() => expect(turnOf().getAttribute("data-turn")).toBe("running"));

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("parked", { message: "Waiting on your approval." });

  await waitFor(() => expect(turnOf().getAttribute("data-turn")).toBe("parked"));
  expect(turnOf().firstElementChild!.getAttribute("class")).toContain("text-blocked");
});

test("a portal row leads with an outline circle, so every row keeps the same left edge", async () => {
  wire({ ...chatsOnWire([CHAT_ROW]) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const railRow = await screen.findByRole("button", { name: /Pick one thread/ });
  const column = railRow.querySelector("[data-turn]")!;
  expect(column.firstElementChild!.className).toContain("rounded-full");
  expect(railRow.querySelector(".tabler-icon-brand-slack")).toBeNull();
});

test("a running thread spins, a held one is marked blocked, and a settled one rests in the same column", async () => {
  holdRailShown(EVERY_SURFACE);
  const running = { ...CHAT_ROW, turn: "running" as const };
  const parked = {
    ...CHAT_ROW,
    conversation_id: "77777777-7777-4777-8777-777777777777",
    title: "Held thread",
    turn: "parked" as const,
  };
  const idle = {
    ...CHAT_ROW,
    conversation_id: "66666666-6666-4666-8666-666666666666",
    title: "Answered thread",
    turn: "idle" as const,
  };
  wire(chatsOnWire([running, parked, idle]));
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const live = await screen.findByRole("button", { name: /Pick one thread/ });
  const held = screen.getByRole("button", { name: /Held thread/ });
  const rest = screen.getByRole("button", { name: /Answered thread/ });
  const column = (button: HTMLElement) => button.querySelector("[data-turn]")!;
  expect(column(live).className).toBe(column(rest).className);
  expect(column(live).className).toContain("size-(--size-glyph)");
  const mark = (button: HTMLElement) => column(button).firstElementChild!.getAttribute("class")!;
  expect(mark(live)).toContain("animate-spin");
  expect(mark(held)).toContain("text-blocked");
  expect(mark(rest)).toContain("text-ink-quiet");
  expect(mark(rest)).not.toContain("animate-spin");
});

test("a stream that drops never rests the row, since the server's turn outlives it", async () => {
  wire({
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello there" }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.type(await screen.findByLabelText("Ask UFO"), "hello there");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  const turnOf = () =>
    screen.getByRole("button", { name: /hello there/ }).querySelector("[data-turn]")!;
  await waitFor(() => expect(turnOf().getAttribute("data-turn")).toBe("running"));

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  const stream = StreamFake.last();
  stream.close();
  stream.fail();

  await waitFor(() => expect(StreamFake.opened.length).toBeGreaterThan(1));
  expect(turnOf().getAttribute("data-turn")).toBe("running");
});

test("bumping a conversation moves it to the top and states the turn it is now in", () => {
  const rows = [row("a", hoursAgo(3)), row("b", hoursAgo(4))];
  const bumped = bumpChat(rows, "b", NOW, "running");
  expect(bumped.map((entry) => entry.conversation_id)).toEqual(["b", "a"]);
  expect(bumped[0].turn).toBe("running");
  expect(bumpChat(bumped, "b", NOW, "idle")[0].turn).toBe("idle");
});

test("priority leads with what is held, then what is working, then the rest by recency", () => {
  const held = { ...row("held", hoursAgo(50)), turn: "parked" as const };
  const working = { ...row("working", hoursAgo(30)), turn: "running" as const };
  const queued = { ...row("queued", hoursAgo(80)), turn: "queued" as const };
  const fresh = row("fresh", hoursAgo(1));
  const stale = row("stale", hoursAgo(9));
  const rows = [fresh, stale, working, held, queued];

  const ranked = railGroups(rows, PORTAL_ONLY, "priority");
  expect(ranked[0].rows.map((entry) => entry.conversation_id)).toEqual([
    "held",
    "working",
    "queued",
    "fresh",
    "stale",
  ]);

  const recent = railGroups(rows, PORTAL_ONLY, RECENCY);
  expect(recent[0].rows.map((entry) => entry.conversation_id)).toEqual(rows.map((r) => r.conversation_id));
});
