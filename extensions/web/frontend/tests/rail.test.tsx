import { readFileSync } from "node:fs";
import { join } from "node:path";

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { NARROW } from "@/lib/narrow";
import { agentName } from "@/lib/agentName";
import { RESTING_STATUS_MS, WORKING_STATUS_MS } from "@/lib/appStatusStore";
import { ADMIN_DISCLOSURE } from "@/lib/audience";
import {
  appOrder,
  appRun,
  bumpChat,
  heldAppsExpanded,
  heldRailShown,
  holdAppsExpanded,
  holdRailShown,
  mergeChats,
  railRows,
  type RailSort,
  stampIso,
  type ChatRow,
} from "@/lib/rail";
import { pickAppsExpanded, pickRailShown, railState, resetRailStore } from "@/lib/railStore";
import { newChatHash } from "@/lib/route";
import type { Agent } from "@/lib/types";

import { AGENT, AGENT_ID, atPhoneWidth, audienceMark, CHAT_APP, CHAT_APP_ID, CHAT_ROW, chatsOnWire, conversationObject, CONVO_ID, destination, json, linked, MEMBER, objectIndex, openAgentRow, SECOND, SECOND_ID, SETTINGS, SITE_KIND, StreamFake, TASK_KIND, TRIGGER_KIND, TURN_ID, useStreamFake, wire } from "./harness";

beforeEach(() => {
  useStreamFake();
});

async function openRail() {
  await userEvent.click(await screen.findByRole("button", { name: "Menu" }));
  const drawer = await screen.findByRole("dialog");
  return within(within(drawer).getByRole("navigation", { name: "Workspace" }));
}

const NOW = new Date(2026, 7, 1, 12, 0, 0);

const NOT_SHARED = "This conversation is not shared with this account.";

const RECENCY: RailSort = "recency";

const PORTAL_ONLY = { terminal: false, slack: false, imessage: false, automations: true };
const EVERY_SURFACE = { terminal: true, slack: true, imessage: true, automations: true };

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
    owner_email: "member@example.com",
    owner_name: null,
    mine: true,
    speaker: null,
    source: null,
    turn: "idle",
    automation_kind: null,
    automation_name: null,
    automation_title: null,
    unread: false,
    archived: false,
    deleted: false,
    pinned: false,
  };
}

function theirs(id: string, last_at: string, speaker: string): ChatRow {
  return { ...row(id, last_at), mine: false, speaker };
}

test("the rail is one run of rows in the order the listing gave, and none where none stand", () => {
  const rows = [
    row("a", hoursAgo(3)),
    row("b", hoursAgo(20)),
    row("c", hoursAgo(4 * 24)),
    row("d", hoursAgo(90 * 24)),
  ];
  expect(railRows(rows, PORTAL_ONLY, RECENCY)).toEqual(rows);
  expect(railRows([], PORTAL_ONLY, RECENCY)).toEqual([]);
});

test("a conversation the member is not in is not in the rail, whoever shared it", () => {
  const rows = [
    row("a", hoursAgo(1)),
    theirs("t1", hoursAgo(2), "Pat Reyes (pat@example.com)"),
    row("b", hoursAgo(20)),
    { ...theirs("t2", hoursAgo(30 * 24), "sam@example.com"), audience: "shared" },
  ];

  expect(railRows(rows, PORTAL_ONLY, RECENCY).map((entry) => entry.conversation_id)).toEqual([
    "a",
    "b",
  ]);
  expect(railRows([theirs("t1", hoursAgo(2), "sam@example.com")], PORTAL_ONLY, RECENCY)).toEqual(
    [],
  );
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

  expect(railRows(rows, PORTAL_ONLY, RECENCY)).toEqual([rows[0]]);

  const withSlack = railRows(rows, { terminal: false, slack: true, imessage: false, automations: true }, RECENCY);
  expect(withSlack.map((entry) => entry.conversation_id)).toEqual(["a", "s1"]);

  const withTerminal = railRows(rows, { terminal: true, slack: false, imessage: false, automations: true }, RECENCY);
  expect(withTerminal.map((entry) => entry.conversation_id)).toEqual(["a", "u1"]);

  const withBoth = railRows(rows, EVERY_SURFACE, RECENCY);
  expect(withBoth.map((entry) => entry.conversation_id)).toEqual(["a", "s1", "u1"]);
});

test("a browser holding no filter admits every surface, and holds what a member names", () => {
  expect(heldRailShown()).toEqual(EVERY_SURFACE);

  holdRailShown({ terminal: true, slack: false, imessage: false, automations: true });
  expect(heldRailShown()).toEqual({ terminal: true, slack: false, imessage: false, automations: true });

  holdRailShown(EVERY_SURFACE);
  expect(heldRailShown()).toEqual(EVERY_SURFACE);

  holdRailShown(PORTAL_ONLY);
  expect(heldRailShown()).toEqual(PORTAL_ONLY);
});

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
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await waitFor(() =>
    expect(railState().rows.map((entry) => entry.title)).toEqual([
      CHAT_ROW.title,
      "Second page thread",
    ]),
  );
  expect(railState().phase).toBe("ready");
  expect(calls).toBe(2);
});

test("the rail's first page stands while the walk is still reading", async () => {
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
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await waitFor(() => expect(railState().phase).toBe("ready"));
  expect(railState().rows.map((entry) => entry.title)).toEqual([CHAT_ROW.title]);

  await act(async () => {
    release();
    await held;
  });
  await waitFor(() => expect(railState().rows).toHaveLength(2));
});

test("the drawer holds the sidebar at a phone width, and a pick shuts it", async () => {
  atPhoneWidth();
  wire({ ...chatsOnWire([CHAT_ROW]) });
  render(<App agents={[AGENT, CHAT_APP]} member={MEMBER} onAgents={() => {}} />);

  expect(screen.queryByRole("navigation", { name: "Workspace" })).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Menu" }));
  const drawer = await screen.findByRole("dialog");
  const nav = within(drawer).getByRole("navigation", { name: "Workspace" });
  expect(within(nav).getByRole("button", { name: "Apps" })).toBeTruthy();

  await userEvent.click(await within(nav).findByRole("button", { name: agentName(CHAT_APP.name) }));

  expect(location.hash).toContain(CHAT_APP_ID);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
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

test("every app the workspace has is drawn, however many there are", () => {
  const many = Array.from({ length: 30 }, (_, at) => app("a" + at, "A" + at));
  expect(appOrder(many, [], () => null).length).toBe(30);
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

test("the query holding the narrow rail's groups open is the theme's own breakpoint", () => {
  const theme = readFileSync(join(import.meta.dirname, "..", "src", "theme.css"), "utf8");
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

test("a deep link waits while its resolve is in flight instead of denying the conversation", async () => {
  location.hash = "#/c/" + CONVO_ID;
  wire({
    "/api/chats": () =>
      new Promise<Response>(() => {
        return;
      }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("Loading…")).toBeTruthy();
  expect(screen.queryByText(NOT_SHARED)).toBeNull();
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
  location.hash = newChatHash(AGENT_ID);
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.type(await screen.findByLabelText("Ask UFO"), "hello there");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  await waitFor(() => expect(location.hash).toBe("#/c/" + CONVO_ID));
  expect(posts[0]).toContain("?conversation=new");
  expect(await screen.findByRole("button", { name: "Visibility: Private" })).toBeTruthy();
  await waitFor(() =>
    expect(railState().rows.map((entry) => entry.conversation_id)).toContain(CONVO_ID),
  );
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
  location.hash = newChatHash(AGENT_ID);
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.type(await screen.findByLabelText("Ask UFO"), "first half");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  // Until that POST answers nothing knows which conversation it opened, and a second send to the `new`
  // sentinel opens another one, so the composer holds the words rather than founding again.
  await userEvent.type(await screen.findByLabelText("Ask UFO"), "second half");
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

  await userEvent.type(await screen.findByLabelText("Ask UFO"), "one thought");
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
  location.hash = newChatHash(AGENT_ID);
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.type(await screen.findByLabelText("Ask UFO"), "early words");
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
  await waitFor(() => expect(railState().rows.length).toBe(2));
  expect(railState().rows.map((entry) => entry.title)).toContain("early words");
  expect(within(screen.getByTestId("log")).getByText("early words")).toBeTruthy();
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
  location.hash = "#/c/" + CONVO_ID;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.type(await screen.findByLabelText("Ask UFO"), "follow-up");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  await waitFor(() =>
    expect(calls.some((url) => url.includes("/chat?conversation=" + CONVO_ID))).toBe(true),
  );
  await waitFor(() =>
    expect(railState().rows.map((entry) => entry.title)[0]).toBe(older.title),
  );
});

test("an origin conversation opens a live comment chat", async () => {
  const slack = {
    ...CHAT_ROW,
    surface: "slack",
    surface_label: "DM",
    title: "Slack question",
  };
  const resolved = {
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
    speakable: true,
    agent: { id: AGENT.id, name: AGENT.name },
  };
  const { calls } = wire({
    ...chatsOnWire([slack]),
    "/api/chats": () => json({ conversation: resolved }),
    "/transcript": () => json({ messages: [{ role: "user", text: "slack words" }] }),
    "/chat": () =>
      json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "Slack question" }),
  });
  location.hash = "#/c/" + CONVO_ID;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("slack words")).toBeTruthy();
  expect(screen.getByLabelText("Ask UFO")).toBeTruthy();

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
  location.hash = "#/c/" + CONVO_ID;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("Work to finish")).toBeTruthy();
  const crumb = within(screen.getByRole("navigation", { name: "Breadcrumb" }));
  expect(crumb.getByText("Daily brief")).toBeTruthy();
  expect(crumb.getByText("Daily-Brief")).toBeTruthy();
  expect(crumb.queryByRole("link")).toBeNull();
  expect(screen.getByLabelText("Ask UFO")).toBeTruthy();
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
    "/api/chats": () => new Response("missing or unknown session cookie", { status: 401 }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("Session ended")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Sign in" }).getAttribute("href")).toBe("/login");
  expect(screen.queryByText(NOT_SHARED)).toBeNull();
});

test("a permalink read the server faults on states the fault rather than a denial", async () => {
  location.hash = "#/c/" + CONVO_ID;
  wire({
    "/api/chats": () => new Response("boom", { status: 500 }),
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
    owner_email: null,
    owner_name: null,
    description: "Warehouse restock plan",
    source: "https://acme.slack.com/archives/C1/p1700000000000100",
    turn_count: 1,
    created_at: "2026-08-01T08:00:00Z",
    last_turn_at: "2026-08-01T08:01:00Z",
    readable: true,
    disclosable: false,
    speakable: true,
    ...fields,
  };
}

test("a Slack conversation permalink opens a live comment chat", async () => {
  location.hash = "#/c/" + CONVO_ID;
  wire({
    "/api/chats": () => json({ conversation: slackConversation() }),
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
    "/api/chats": () => json({ conversation: slackConversation() }),
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
    "/api/chats": () => json({ conversation: terminal }),
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
});

test("a read-only conversation is headed by who reads it, as its projection carried", async () => {
  location.hash = "#/c/" + CONVO_ID;
  const readOnly = slackConversation({ speakable: false });
  wire({
    "/api/chats": () => json({ conversation: readOnly }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByText("No messages in this conversation yet.");
  expect(screen.queryByLabelText("Ask UFO")).toBeNull();
  const mark = audienceMark();
  expect(mark.getAttribute("title")).toBe(
    "Every member of the workspace reads this conversation. " + ADMIN_DISCLOSURE,
  );
  fireEvent.focus(mark);
  expect((await screen.findByRole("tooltip")).textContent).toBe("Workspace");
});

test("a conversation an admin opened states the member it is private to", async () => {
  location.hash = "#/c/" + CONVO_ID;
  const other = slackConversation({
    speakable: false,
    audience: "member:0a1b2c3d-0000-4000-8000-000000000009",
    member_email: "mel@example.com",
  });
  wire({
    "/api/chats": () => json({ conversation: other }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={{ ...MEMBER, admin: true }} onAgents={() => {}} />);

  await screen.findByText("No messages in this conversation yet.");
  expect(screen.queryByLabelText("Ask UFO")).toBeNull();
  const mark = audienceMark();
  expect(mark.getAttribute("title")).toBe(
    "Only mel@example.com reads this conversation. " + ADMIN_DISCLOSURE,
  );
  fireEvent.focus(mark);
  expect((await screen.findByRole("tooltip")).textContent).toBe("Private to mel@example.com");
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
    "/api/chats": () =>
      json({
        conversation: {
          id: CONVO_ID,
          agent: { id: AGENT_ID, name: AGENT.name },
          surface: "slack",
          surface_label: "#ops-warehouse",
          audience: "shared",
          member_email: null,
          owner_email: null,
          owner_name: null,
          source: "https://acme.slack.com/archives/C1/p1700000000000100",
          turn_count: 1,
          created_at: "2026-08-01T08:00:00Z",
          last_turn_at: "2026-08-01T08:01:00Z",
          readable: true,
          disclosable: false,
          speakable: true,
        },
      }),
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

test("the ask row opens the chat app at its start screen when one is shipped", async () => {
  atPhoneWidth();
  location.hash = "#/";
  wire({});
  render(<App agents={[AGENT, CHAT_APP]} member={MEMBER} onAgents={() => {}} />);

  const rail = await openRail();
  await userEvent.click(rail.getByRole("button", { name: "New chat" }));

  expect(location.hash).toBe("#/agents/" + CHAT_APP_ID + "?open=compose");
});

test("the ask control targets the main agent, and offers no other", async () => {
  atPhoneWidth();
  wire({});
  const single = render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await userEvent.click((await openRail()).getByRole("button", { name: "New chat" }));
  expect(location.hash).toBe("#/new/" + AGENT_ID);
  single.unmount();

  location.hash = "";
  wire({});
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);
  await userEvent.click((await openRail()).getByRole("button", { name: "New chat" }));

  expect(location.hash).toBe("#/new/" + AGENT_ID);
  expect(await screen.findByLabelText("Ask UFO")).toBeTruthy();
  expect(screen.queryByRole("navigation", { name: "Breadcrumb" })).toBeNull();
});

test("the apps list opens the agent's page, and the sidebar starts the conversation", async () => {
  atPhoneWidth();
  location.hash = "#/";
  wire({ "/settings": () => json(SETTINGS), "/connections": () => json({ connections: [] }) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await screen.findByRole("main");
  await openRail();

  await openAgentRow("Assistant");
  expect(location.hash).toBe("#/agents/" + AGENT_ID);

  const rail = await openRail();
  expect(rail.getByRole("navigation", { name: "Apps" })).toBeTruthy();
  await userEvent.click(rail.getByRole("button", { name: "New chat" }));
  expect(location.hash).toBe("#/new/" + AGENT_ID);
  expect(await screen.findByLabelText("Ask UFO")).toBeTruthy();
  expect(screen.queryByRole("navigation", { name: "Breadcrumb" })).toBeNull();
});

test("an app with nothing in flight states nothing under its name", async () => {
  atPhoneWidth();
  location.hash = "#/";
  wire({
    "/settings": () => json(SETTINGS),
    "/connections": () => json({ connections: [] }),
    "/api/agents/status": () =>
      json({ statuses: [{ agent_id: AGENT_ID, turn: null, last_failed: false }] }),
  });
  const purposeful = { ...AGENT, purpose: "Answers from what this workspace has recorded." };
  render(<App agents={[purposeful]} member={MEMBER} onAgents={() => {}} />);
  await screen.findByRole("main");
  const rail = await openRail();

  const index = within(rail.getByRole("navigation", { name: "Apps" }));
  const row = await index.findByRole("button", { name: /^Assistant/ });
  expect(within(row).queryByText("Answers from what this workspace has recorded.")).toBeNull();
  expect(index.queryByText(/Idle|Active/)).toBeNull();
  expect(row.textContent).toBe("Assistant");
});

test("the chat header names the agent holding the conversation and what it is called, never the model", async () => {
  wire({
    ...chatsOnWire([CHAT_ROW]),
    "/transcript": () => json({ messages: [] }),
    "/settings": () => new Response("nope", { status: 503 }),
  });
  location.hash = "#/c/" + CONVO_ID;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const crumb = within(await screen.findByRole("navigation", { name: "Breadcrumb" }));
  expect(crumb.getByText("Pick one thread")).toBeTruthy();
  expect(crumb.getByText("Assistant")).toBeTruthy();
  expect(crumb.queryByText(AGENT.model)).toBeNull();

  await userEvent.click(crumb.getByRole("link", { name: "Back to Assistant" }));
  expect(location.hash).toBe("#/new/" + AGENT_ID);
});

test("a deep link is not blamed while the rail is the thing that failed", async () => {
  location.hash = "#/c/" + CONVO_ID;
  wire({
    ...chatsOnWire([CHAT_ROW]),
    "/objects/conversation$": () => new Response("nope", { status: 500 }),
    "/transcript": () => json({ messages: [{ role: "user", text: "linked words" }] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("linked words")).toBeTruthy();
  expect(await screen.findByText("Conversations did not refresh.")).toBeTruthy();
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
  location.hash = "#/c/" + CONVO_ID;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.type(await screen.findByLabelText("Ask UFO"), "doomed");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  expect(await screen.findByText("Error 500 — try again.")).toBeTruthy();
  expect(railState().rows.map((entry) => entry.title)[0]).toBe(newer.title);
});

test("a linked conversation past the rail's bound resolves by id", async () => {
  location.hash = "#/c/" + CONVO_ID;
  wire({
    "/api/chats": () => json({ conversation: linked(CHAT_ROW) }),
    "/transcript": () => json({ messages: [{ role: "user", text: "linked words" }] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("linked words")).toBeTruthy();
  expect(
    within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByText(CHAT_ROW.title),
  ).toBeTruthy();
  expect(railState().rows).toEqual([]);
});

test("a hash naming an agent this member cannot reach reports it", async () => {
  location.hash = "#/agents/99999999-9999-4999-8999-999999999999";
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("No such app.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /^Menu for/ })).toBeNull();
});

test("the sidebar marks the destination the member is in and leaves the others off", async () => {
  atPhoneWidth();
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

  const marked = (rail: ReturnType<typeof within>) =>
    ["New chat", "Connections", "Workspace"].filter(
      (name) => rail.getByRole("button", { name }).getAttribute("aria-current") === "true",
    );

  const home = await openRail();
  expect(marked(home)).toEqual(["New chat"]);

  await userEvent.click(home.getByRole("button", { name: "Workspace" }));
  await waitFor(() => expect(destination()).toBe("Team"));
  const team = await openRail();
  expect(marked(team)).toEqual(["Workspace"]);

  await userEvent.click(team.getByRole("button", { name: "Connections" }));
  const connectors = await openRail();
  await waitFor(() => expect(marked(connectors)).toEqual(["Connections"]));

  const index = within(connectors.getByRole("navigation", { name: "Apps" }));
  expect(index.getByRole("button", { name: "Assistant" })).toBeTruthy();
  expect(index.getByRole("button", { name: "Second" })).toBeTruthy();
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

  const ranked = railRows(rows, PORTAL_ONLY, "priority");
  expect(ranked.map((entry) => entry.conversation_id)).toEqual([
    "held",
    "working",
    "queued",
    "fresh",
    "stale",
  ]);

  const recent = railRows(rows, PORTAL_ONLY, RECENCY);
  expect(recent.map((entry) => entry.conversation_id)).toEqual(rows.map((r) => r.conversation_id));
});

test("a permalink the resolve refuses is unshared, whatever the rail lists", async () => {
  location.hash = "#/c/" + CONVO_ID;
  wire({
    ...chatsOnWire([CHAT_ROW]),
    "/api/chats": () => new Response("no such conversation", { status: 404 }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText(NOT_SHARED)).toBeTruthy();
  await waitFor(() =>
    expect(railState().rows.map((entry) => entry.conversation_id)).toContain(CONVO_ID),
  );
  expect(screen.queryByLabelText("Ask UFO")).toBeNull();
});

test("a permalink to a conversation an admin may only disclose offers the acknowledgement first", async () => {
  location.hash = "#/c/" + CONVO_ID;
  const { calls } = wire({
    "/api/chats": () =>
      json({
        conversation: slackConversation({
          audience: "member:0a1b2c3d-0000-4000-8000-000000000009",
          member_email: "mel@example.com",
          readable: false,
          disclosable: true,
          speakable: false,
        }),
      }),
    "/actions/conversation/": () => json({ actions: [] }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={{ ...MEMBER, admin: true }} onAgents={() => {}} />);

  expect(await screen.findByText(/private to mel@example.com/)).toBeTruthy();
  expect(screen.queryByLabelText("Ask UFO")).toBeNull();
  expect(calls.some((url) => url.includes("/transcript"))).toBe(false);
});

test("a conversation the member reads but may not speak in draws no composer", async () => {
  location.hash = "#/c/" + CONVO_ID;
  wire({
    "/api/chats": () => json({ conversation: slackConversation({ speakable: false }) }),
    "/transcript": () => json({ messages: [{ role: "user", text: "from Slack" }] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("from Slack")).toBeTruthy();
  expect(screen.queryByLabelText("Ask UFO")).toBeNull();
  expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
});

test("the rail's sound mark names the act it does, and this browser holds the pick", async () => {
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const rail = within(await screen.findByRole("navigation", { name: "Tabs" }));
  const mark = rail.getByRole("button", { name: "Mute sounds" });
  expect(mark.getAttribute("aria-pressed")).toBe("false");

  await userEvent.click(mark);

  const silenced = rail.getByRole("button", { name: "Unmute sounds" });
  expect(silenced.getAttribute("aria-pressed")).toBe("true");
  expect(localStorage.getItem("ufo.sound-muted")).toBe("muted");
});

test("a browser that was muted opens on the mark that unmutes it", async () => {
  localStorage.setItem("ufo.sound-muted", "muted");
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const rail = within(await screen.findByRole("navigation", { name: "Tabs" }));
  expect(rail.getByRole("button", { name: "Unmute sounds" }).getAttribute("aria-pressed")).toBe(
    "true",
  );
  expect(rail.queryByRole("button", { name: "Mute sounds" })).toBeNull();
});
