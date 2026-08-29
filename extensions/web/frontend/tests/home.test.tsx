import { act, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import {
  holdTurn,
  moveTurnHold,
  releaseTurn,
  statusDot,
  useAppStatus,
  type AgentStatus,
} from "@/lib/appStatusStore";
import {
  HOME_NEW_LANE,
  homeConversationLane,
  homeHash,
  homeLaneAgent,
  mintHomeLane,
} from "@/lib/route";
import { TRACK_MAX_SLOTS } from "@/lib/tracks";

import {
  AGENT,
  AGENT_ID,
  CHAT_ROW,
  chatsOnWire,
  CONVO_ID,
  json,
  MEMBER,
  SECOND,
  SECOND_ID,
  useStreamFake,
  wire,
} from "./harness";

const OTHER_CONVO_ID = "66666666-6666-4666-8666-666666666666";
const PRIVATE_CONVO_ID = "77777777-7777-4777-8777-777777777777";

const AGENT_INSTANCE = mintHomeLane(AGENT_ID, [AGENT_ID]);
const CONVERSATION_LANE = homeConversationLane(CONVO_ID);

/** A row at the cap, the two apps standing turn and turn about, each lane its own instance. */
const FULL_ROW = Array.from({ length: TRACK_MAX_SLOTS }).reduce<string[]>(
  (row, _, at) => [...row, mintHomeLane(at % 2 ? SECOND_ID : AGENT_ID, row)],
  [],
);

const laneName = (lane: string): string => (homeLaneAgent(lane) === AGENT_ID ? "Assistant" : "Second");

const standingLanes = (): HTMLElement[] =>
  Array.from(document.querySelectorAll<HTMLElement>("[data-slot=slot-track] > div > section"));

const laneNames = (): string[] =>
  standingLanes().map((lane) => lane.getAttribute("aria-label") ?? "");

const laneMark = (lane: HTMLElement): string | null =>
  lane.querySelector("[data-slot=header] svg[class*=element-icon-]")?.getAttribute("class") ?? null;

const drawHome = (opens: string[]) => {
  location.hash = homeHash({ opens });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);
};

const openPicker = async (): Promise<HTMLElement> => {
  await userEvent.click(await screen.findByRole("button", { name: "New tab" }));
  return await screen.findByRole("region", { name: "New tab" });
};

const status = (held: Partial<AgentStatus>): AgentStatus => ({
  agent_id: AGENT_ID,
  turn: null,
  activity: null,
  last_active_at: null,
  last_failed: false,
  ...held,
});

beforeEach(() => {
  // The history's ladder and Show set are held in this browser, so one test's picks must not open
  // the next one's lanes.
  localStorage.clear();
  useStreamFake();
});

test("home stands one lane per app the address opens, in the order it opens them", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([SECOND_ID, AGENT_ID]);

  await screen.findByRole("region", { name: "Second" });
  expect(laneNames()).toEqual(["Second", "Assistant"]);

  const [second, assistant] = standingLanes();
  expect(laneMark(second)).toContain("element-icon-aten");
  expect(laneMark(assistant)).toContain("element-icon-propylon");
});

test("closing a lane cuts it from the address and leaves the rest standing", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([SECOND_ID, AGENT_ID]);

  await screen.findByRole("region", { name: "Second" });
  await userEvent.click(screen.getByRole("button", { name: "Close Second" }));

  await waitFor(() => expect(location.hash).toBe(homeHash({ opens: [AGENT_ID] })));
  expect(laneNames()).toEqual(["Assistant"]);
});

test("expanding a lane hides its siblings until its return act restores the row", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([SECOND_ID, AGENT_ID]);

  await screen.findByRole("region", { name: "Assistant" });
  const expand = screen.getByRole("button", { name: "Expand Assistant" });
  expect(expand.querySelector(".tabler-icon-arrows-diagonal")).toBeTruthy();
  await userEvent.click(expand);

  expect(screen.queryByRole("region", { name: "Second" })).toBeNull();
  expect(screen.getByRole("region", { name: "Assistant" })).toBeTruthy();
  expect(location.hash).toBe(homeHash({ opens: [SECOND_ID, AGENT_ID] }));
  expect(
    document
      .querySelector<HTMLElement>("[data-slot=slot-track]")
      ?.style.getPropertyValue("--lane-share"),
  ).toBe("1");

  const showAll = screen.getByRole("button", { name: "Show all lanes" });
  expect(showAll.querySelector(".tabler-icon-arrows-diagonal-minimize-2")).toBeTruthy();
  expect(showAll.querySelector(".tabler-icon-x")).toBeNull();
  await userEvent.click(showAll);

  expect(laneNames()).toEqual(["Second", "Assistant"]);
  expect(location.hash).toBe(homeHash({ opens: [SECOND_ID, AGENT_ID] }));
});

test("double-clicking a lane band expands it and restores the row", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([SECOND_ID, AGENT_ID]);

  const assistant = await screen.findByRole("region", { name: "Assistant" });
  await userEvent.dblClick(assistant.querySelector("[data-slot=header]")!);

  expect(screen.queryByRole("region", { name: "Second" })).toBeNull();
  expect(screen.getByRole("button", { name: "Show all lanes" })).toBeTruthy();

  await userEvent.dblClick(
    screen.getByRole("region", { name: "Assistant" }).querySelector("[data-slot=header]")!,
  );

  expect(laneNames()).toEqual(["Second", "Assistant"]);
  expect(location.hash).toBe(homeHash({ opens: [SECOND_ID, AGENT_ID] }));
});

test("the rail's new tab leaves an expanded lane and opens the picker", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([SECOND_ID, AGENT_ID]);

  await screen.findByRole("region", { name: "Assistant" });
  await userEvent.click(screen.getByRole("button", { name: "Expand Assistant" }));
  await openPicker();

  expect(screen.queryByRole("button", { name: "Show all lanes" })).toBeNull();
  expect(location.hash).toBe(homeHash({ opens: [HOME_NEW_LANE, SECOND_ID, AGENT_ID] }));
  expect(laneNames()).toEqual(["New tab", "Second", "Assistant"]);
});

test("a rail app switches the expanded lane and keeps it expanded", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([SECOND_ID, AGENT_ID]);

  await screen.findByRole("region", { name: "Assistant" });
  await userEvent.click(screen.getByRole("button", { name: "Expand Assistant" }));
  await userEvent.click(
    within(screen.getByRole("navigation", { name: "Tabs" })).getByRole("button", {
      name: "Second",
    }),
  );

  expect(screen.queryByRole("region", { name: "Assistant" })).toBeNull();
  expect(screen.getByRole("region", { name: "Second" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Show all lanes" })).toBeTruthy();
  expect(location.hash).toBe(homeHash({ opens: [SECOND_ID, AGENT_ID] }));
});

/** A pick made inside an expanded lane opens what it picked in that lane's place, so the expansion
 *  is left naming a lane the row no longer holds. The row stands whole again there, with every lane
 *  shown: what one lane's expansion hides, the end of it gives back. */
test("a conversation picked inside an expanded lane opens it and stands the row again", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([SECOND_ID, AGENT_ID]);

  await screen.findByRole("region", { name: "Assistant" });
  await userEvent.click(screen.getByRole("button", { name: "Expand Assistant" }));
  await userEvent.click(screen.getByRole("button", { name: "History for Assistant" }));
  const history = await screen.findByRole("region", { name: "History" });
  await userEvent.click(
    within(history).getByRole("button", { name: new RegExp("^" + CHAT_ROW.title) }),
  );

  await waitFor(() =>
    expect(location.hash).toBe(homeHash({ opens: [SECOND_ID, CONVERSATION_LANE] })),
  );
  expect(laneNames()).toEqual(["Second", CHAT_ROW.title]);
  expect(standingLanes().map((lane) => lane.hidden)).toEqual([false, false]);
  expect(screen.queryByRole("button", { name: "Show all lanes" })).toBeNull();
});

test("an address arriving with a picker lands without it", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([AGENT_ID, HOME_NEW_LANE]);

  await waitFor(() => expect(location.hash).toBe(homeHash({ opens: [AGENT_ID] })));
  expect(laneNames()).toEqual(["Assistant"]);
});

test("the rail's home mark keeps the row the member arranged", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([SECOND_ID, AGENT_ID]);
  await waitFor(() => expect(laneNames()).toEqual(["Second", "Assistant"]));

  await userEvent.click(await screen.findByRole("button", { name: "Home" }));

  expect(location.hash).toBe(homeHash({ opens: [SECOND_ID, AGENT_ID] }));
  expect(laneNames()).toEqual(["Second", "Assistant"]);
});

test("the rail's new tab opens the picker from another screen's rail too", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([AGENT_ID]);
  await waitFor(() => expect(laneNames()).toEqual(["Assistant"]));
  await userEvent.click(await screen.findByRole("button", { name: "Workspace" }));
  await waitFor(() => expect(location.hash).not.toBe(homeHash({ opens: [AGENT_ID] })));

  await openPicker();

  expect(location.hash).toBe(homeHash({ opens: [HOME_NEW_LANE, AGENT_ID] }));
  expect(laneNames()).toEqual(["New tab", "Assistant"]);
});

test("the rail's new tab enters the picker at the left while the row is under the cap", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([AGENT_ID, SECOND_ID]);

  await openPicker();

  expect(location.hash).toBe(homeHash({ opens: [HOME_NEW_LANE, AGENT_ID, SECOND_ID] }));
  expect(laneNames()).toEqual(["New tab", "Assistant", "Second"]);
});

test("the rail's new tab at the cap drops the lane standing at the right end", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome(FULL_ROW);

  await waitFor(() => expect(laneNames()).toHaveLength(TRACK_MAX_SLOTS));
  await openPicker();

  const kept = FULL_ROW.slice(0, -1);
  expect(location.hash).toBe(homeHash({ opens: [HOME_NEW_LANE, ...kept] }));
  expect(laneNames()).toEqual(["New tab", ...kept.map(laneName)]);
});

/** The row may be wider than the screen, and the rail's tile is how a lane that scrolled off it is
 *  reached: the press brings that lane into view and leaves the row in the order the member arranged
 *  it. Two presses are two asks — the member scrolled away between them. */
test("the rail's tile brings its lane into view and leaves the row where it stood", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  const scrolled: string[] = [];
  const scrolls = vi
    .spyOn(Element.prototype, "scrollIntoView")
    .mockImplementation(function (this: Element) {
      scrolled.push(this.getAttribute("aria-label") ?? "");
    });
  try {
    drawHome([AGENT_ID, SECOND_ID]);
    await waitFor(() => expect(laneNames()).toEqual(["Assistant", "Second"]));
    expect(scrolled).toEqual([]);
    const rail = screen.getByRole("navigation", { name: "Tabs" });

    await userEvent.click(within(rail).getByRole("button", { name: "Second" }));

    expect(scrolled).toEqual(["Second"]);
    expect(location.hash).toBe(homeHash({ opens: [AGENT_ID, SECOND_ID] }));
    expect(laneNames()).toEqual(["Assistant", "Second"]);

    await userEvent.click(within(rail).getByRole("button", { name: "Second" }));

    expect(scrolled).toEqual(["Second", "Second"]);
    expect(location.hash).toBe(homeHash({ opens: [AGENT_ID, SECOND_ID] }));
  } finally {
    scrolls.mockRestore();
  }
});

test("the rail's tile from another screen lands home with that lane in view", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  const scrolled: string[] = [];
  const scrolls = vi
    .spyOn(Element.prototype, "scrollIntoView")
    .mockImplementation(function (this: Element) {
      scrolled.push(this.getAttribute("aria-label") ?? "");
    });
  try {
    drawHome([AGENT_ID, SECOND_ID]);
    await waitFor(() => expect(laneNames()).toEqual(["Assistant", "Second"]));
    await userEvent.click(await screen.findByRole("button", { name: "Workspace" }));
    await waitFor(() => expect(laneNames()).toEqual([]));
    const rail = screen.getByRole("navigation", { name: "Tabs" });

    await userEvent.click(within(rail).getByRole("button", { name: "Second" }));

    expect(location.hash).toBe(homeHash({ opens: [AGENT_ID, SECOND_ID] }));
    await waitFor(() => expect(laneNames()).toEqual(["Assistant", "Second"]));
    expect(scrolled).toEqual(["Second"]);
  } finally {
    scrolls.mockRestore();
  }
});

test("the rail's new tab stands the picker at most once", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([AGENT_ID]);

  await openPicker();
  await userEvent.click(screen.getByRole("button", { name: "New tab" }));

  expect(location.hash).toBe(homeHash({ opens: [HOME_NEW_LANE, AGENT_ID] }));
  expect(laneNames()).toEqual(["New tab", "Assistant"]);
});

/** The picker offers the apps and nothing else. The member's history is read in the chat lane, where
 *  it stands over the entry the next conversation starts in, so the picker never lists it twice. */
test("the picker lane offers the workspace's apps under one head", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([AGENT_ID]);

  const picker = await openPicker();
  expect(laneMark(picker)).toBeNull();

  const apps = within(picker).getByRole("heading", { name: "Apps", level: 3 });
  expect(
    within(apps.parentElement!)
      .getAllByRole("button")
      .map((row) => row.textContent),
  ).toEqual(["Assistant", "Second"]);
  expect(within(picker).queryByRole("heading", { name: "History" })).toBeNull();
  expect(within(picker).queryByText(CHAT_ROW.title)).toBeNull();
});

/** The chat lane opens on the member's own history: the conversations stand over the entry the next
 *  one starts in, each named by its source where it came in somewhere else, and the runs are the day
 *  each one last moved. */
test("the chat lane's unsaid state stands the member's history over the new chat entry", async () => {
  const terminal = {
    ...CHAT_ROW,
    conversation_id: OTHER_CONVO_ID,
    title: "Ship the ledger",
    surface: "ufo",
    last_at: new Date().toISOString(),
  };
  wire(chatsOnWire([{ ...CHAT_ROW, last_at: new Date().toISOString() }, terminal]));
  drawHome([AGENT_ID]);

  const lane = await screen.findByRole("region", { name: "Assistant" });
  await within(lane).findByRole("heading", { name: "History", level: 3 });
  expect(within(lane).getByRole("heading", { name: "Today", level: 4 })).toBeTruthy();
  const rows = within(lane)
    .getAllByRole("button")
    .map((row) => row.textContent ?? "");
  expect(rows.some((row) => row.includes(CHAT_ROW.title))).toBe(true);
  expect(rows.some((row) => row.includes(terminal.title) && row.includes("Terminal"))).toBe(true);
  // A portal chat states no source: the history is read in the portal.
  expect(rows.filter((row) => row.includes("Terminal"))).toHaveLength(1);
});

/** The ladder and the surfaces the member keeps are the history's own narrowings, held in this
 *  browser: a member who asked to see their terminal sessions asked about their own history. */
test("the history runs the rows in the ladder the member picks and drops the surfaces they put away", async () => {
  const terminal = {
    ...CHAT_ROW,
    conversation_id: OTHER_CONVO_ID,
    title: "Ship the ledger",
    surface: "ufo",
    agent_name: "second",
    agent_id: SECOND_ID,
  };
  wire(chatsOnWire([CHAT_ROW, terminal]));
  drawHome([AGENT_ID]);

  const lane = await screen.findByRole("region", { name: "Assistant" });
  await within(lane).findByRole("heading", { name: "History", level: 3 });

  await userEvent.click(within(lane).getByRole("button", { name: "History options" }));
  await userEvent.click(await screen.findByRole("menuitemradio", { name: "App" }));

  expect(within(lane).getByRole("heading", { name: "Assistant", level: 4 })).toBeTruthy();
  expect(within(lane).getByRole("heading", { name: "Second", level: 4 })).toBeTruthy();
  expect(localStorage.getItem("chat-ladder")).toBe("app");

  await userEvent.click(within(lane).getByRole("button", { name: "History options" }));
  await userEvent.click(await screen.findByRole("menuitemcheckbox", { name: "Terminal" }));

  await waitFor(() => expect(within(lane).queryByText(/Ship the ledger/)).toBeNull());
  expect(localStorage.getItem("chat-hidden")).toBe("ufo");
  expect(within(lane).getByText(new RegExp(CHAT_ROW.title))).toBeTruthy();
});

/** The two narrowings are the browser's, not one list's: a member who puts a surface away in the lane
 *  they are reading has put it away in their history, and the lane beside it says so where it stands
 *  rather than on the next visit. */
test("a narrowing taken in one lane's history stands in the lane beside it", async () => {
  const terminal = {
    ...CHAT_ROW,
    conversation_id: OTHER_CONVO_ID,
    title: "Ship the ledger",
    surface: "ufo",
    agent_name: "second",
    agent_id: SECOND_ID,
  };
  wire(chatsOnWire([CHAT_ROW, terminal]));
  drawHome([AGENT_ID, AGENT_INSTANCE]);

  await waitFor(() => expect(laneNames()).toEqual(["Assistant", "Assistant"]));
  const [first, second] = standingLanes();
  await within(second).findByRole("heading", { name: "History", level: 3 });
  expect(within(second).getByText(/Ship the ledger/)).toBeTruthy();

  await userEvent.click(within(first).getByRole("button", { name: "History options" }));
  await userEvent.click(await screen.findByRole("menuitemcheckbox", { name: "Terminal" }));

  await waitFor(() => expect(within(second).queryByText(/Ship the ledger/)).toBeNull());
  await userEvent.keyboard("{Escape}");

  // And the menu the other lane opens ticks what the member took, rather than what that lane
  // mounted holding.
  await userEvent.click(within(second).getByRole("button", { name: "History options" }));
  expect(
    (await screen.findByRole("menuitemcheckbox", { name: "Terminal" })).getAttribute("aria-checked"),
  ).toBe("false");
});

test("a chat lane's history lists the member's own conversations and groups colleagues' under one heading", async () => {
  const colleague = {
    ...CHAT_ROW,
    conversation_id: OTHER_CONVO_ID,
    title: "Ship the ledger",
    mine: false,
    speaker: "alex@metalcraft.ai",
  };
  const { calls } = wire({
    ...chatsOnWire([CHAT_ROW, colleague]),
    "/conversations$": () =>
      json({
        conversations: [
          {
            id: PRIVATE_CONVO_ID,
            agent: null,
            surface: "slack",
            surface_label: "#ops",
            audience: "member:justin",
            member_email: "justin@simplecasual.com",
            description: "",
            source: null,
            speakers: [],
            turn_count: 3,
            created_at: "2026-08-01T09:00:00",
            last_turn_at: "2026-08-01T09:00:00",
            readable: false,
            disclosable: true,
            commentable: false,
          },
        ],
        more: false,
      }),
  });
  drawHome([AGENT_ID]);

  await userEvent.click(await screen.findByRole("button", { name: "History for Assistant" }));
  const lane = await screen.findByRole("region", { name: "History" });

  const rows = within(lane)
    .getAllByRole("button")
    .map((row) => row.textContent ?? "")
    .filter(Boolean);
  expect(rows).toHaveLength(2);
  expect(rows[0]).toContain(CHAT_ROW.title);
  expect(rows[1]).toContain(colleague.title);
  expect(within(lane).getByRole("heading", { name: "Other members", level: 3 })).toBeTruthy();
  expect(within(lane).queryByText(/justin@simplecasual.com/)).toBeNull();
  expect(calls.some((url) => url.includes("/agents/" + AGENT_ID + "/conversations"))).toBe(false);
});

test("picking an app not yet standing takes the picker lane's place in the address", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([AGENT_ID]);

  const picker = await openPicker();
  await userEvent.click(within(picker).getByRole("button", { name: "Second" }));

  await waitFor(() => expect(location.hash).toBe(homeHash({ opens: [SECOND_ID, AGENT_ID] })));
  expect(laneNames()).toEqual(["Second", "Assistant"]);
});

test("picking an app already standing mints a second instance in the picker lane's place", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([AGENT_ID]);

  const picker = await openPicker();
  await userEvent.click(within(picker).getByRole("button", { name: "Assistant" }));

  await waitFor(() => expect(location.hash).toBe(homeHash({ opens: [AGENT_INSTANCE, AGENT_ID] })));
  expect(laneNames()).toEqual(["Assistant", "Assistant"]);
});

test("picking a conversation off the chat lane's history takes that lane over", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([AGENT_ID, SECOND_ID]);

  const lane = await screen.findByRole("region", { name: "Assistant" });
  await within(lane).findByRole("heading", { name: "History", level: 3 });
  await userEvent.click(within(lane).getByRole("button", { name: new RegExp(CHAT_ROW.title) }));

  await waitFor(() =>
    expect(location.hash).toBe(homeHash({ opens: [CONVERSATION_LANE, SECOND_ID] })),
  );
  expect(laneNames()).toEqual([CHAT_ROW.title, "Second"]);
});

test("picking a conversation already standing closes the picking lane instead of doubling it", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([AGENT_ID, CONVERSATION_LANE]);

  const lane = await screen.findByRole("region", { name: "Assistant" });
  await screen.findByRole("region", { name: CHAT_ROW.title });
  await within(lane).findByRole("heading", { name: "History", level: 3 });
  await userEvent.click(within(lane).getByRole("button", { name: new RegExp(CHAT_ROW.title) }));

  await waitFor(() => expect(location.hash).toBe(homeHash({ opens: [CONVERSATION_LANE] })));
  expect(laneNames()).toEqual([CHAT_ROW.title]);
});

test("the dot is live while an app works, blocked while it waits on the member, and nothing at rest", () => {
  expect(statusDot(status({ turn: "running" }), false)).toBe("bg-live");
  expect(statusDot(status({ turn: "queued" }), false)).toBe("bg-live");
  expect(statusDot(status({ turn: "running" }), true)).toBe("bg-live");

  const blocked = "bg-blocked animate-working motion-reduce:animate-none";
  expect(statusDot(status({ turn: "parked" }), false)).toBe(blocked);
  expect(statusDot(status({ last_failed: true }), false)).toBe(blocked);
  expect(statusDot(undefined, true)).toBe(blocked);

  expect(statusDot(status({}), false)).toBeNull();
  expect(statusDot(undefined, false)).toBeNull();
});

test("a turn held here marks its app at work until the hold is released", () => {
  wire({ "/api/agents/status": () => json({ statuses: [] }) });
  const view = renderHook(() => useAppStatus());

  act(() => holdTurn(AGENT_ID, AGENT_ID));
  expect(view.result.current.statuses[AGENT_ID].turn).toBe("queued");
  expect(view.result.current.working).toBe(true);

  act(() => releaseTurn(AGENT_ID));
  expect(view.result.current.statuses[AGENT_ID]).toBeUndefined();
  expect(view.result.current.working).toBe(false);
});

test("a wire answer that already shows the app running keeps the wire's own row", async () => {
  wire({
    "/api/agents/status": () =>
      json({
        statuses: [
          status({
            turn: "running",
            activity: "reading the inbox",
            last_active_at: "2026-08-01T09:00:00",
          }),
        ],
      }),
  });
  const view = renderHook(() => useAppStatus());

  act(() => holdTurn(AGENT_ID, AGENT_ID));
  await waitFor(() => expect(view.result.current.statuses[AGENT_ID].turn).toBe("running"));
  expect(view.result.current.statuses[AGENT_ID].activity).toBe("reading the inbox");
  expect(view.result.current.working).toBe(true);
});

test("a founding lane's hold moves to the conversation the send opened", () => {
  wire({ "/api/agents/status": () => json({ statuses: [] }) });
  const view = renderHook(() => useAppStatus());

  act(() => holdTurn(AGENT_ID, AGENT_ID));
  act(() => moveTurnHold(AGENT_ID, CONVO_ID));

  act(() => releaseTurn(AGENT_ID));
  expect(view.result.current.statuses[AGENT_ID].turn).toBe("queued");

  act(() => releaseTurn(CONVO_ID));
  expect(view.result.current.statuses[AGENT_ID]).toBeUndefined();
  expect(view.result.current.working).toBe(false);
});

/** Every other lane's band indents its name after a glyph; the picker's carries the mark of the
 *  rail's New tab tile, so its name lines up with the rest instead of standing flush left. */
test("the picker lane's band carries the New tab glyph", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([AGENT_ID]);

  const picker = await openPicker();
  expect(picker.querySelector("[data-slot=header] svg.tabler-icon-plus")).not.toBeNull();
});

/** Every act on a lane's band is a mark and nothing else: the glyph, the muted tone the marks
 *  around it take, and no box. A 32-pixel control among them would set the row's spacing from its
 *  own width, and the row would no longer read at the pitch the band is drawn at. Nothing stands
 *  there to state the handle either — the band itself is what a reorder is carried by. */
test("a lane band's acts are marks in the muted tone, and no grip stands among them", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([AGENT_ID, SECOND_ID]);

  const lane = await screen.findByRole("region", { name: "Assistant" });
  const band = lane.querySelector("[data-slot=header]")!;
  const marks = Array.from(band.querySelectorAll("button"));
  expect(marks.map((mark) => mark.getAttribute("aria-label"))).toEqual([
    "History for Assistant",
    "Expand Assistant",
    "Close Assistant",
  ]);
  for (const mark of marks) {
    expect(mark.className).toContain("size-(--size-glyph)");
    expect(mark.className).toContain("text-ink-soft");
    expect(mark.querySelector("svg")!.getAttribute("stroke-width")).toBe("1.25");
  }
  expect(band.querySelector("svg.tabler-icon-grip-vertical")).toBeNull();
});

/** Held, the act darkens to the page's own ink. A filled box behind a 16-pixel mark is wider than
 *  the gap between it and the mark beside it, so the pressed state has to be the ink itself. */
test("a lane's history act states its hold in ink rather than in a filled box", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([AGENT_ID]);

  const act = await screen.findByRole("button", { name: "History for Assistant" });
  expect(act.getAttribute("aria-pressed")).toBe("false");

  await userEvent.click(act);

  expect(act.getAttribute("aria-pressed")).toBe("true");
  expect(act.className).toContain("text-ink");
  expect(act.className).not.toContain("bg-fill");
});
