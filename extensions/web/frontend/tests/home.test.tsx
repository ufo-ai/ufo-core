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
 *  reached: the press carries that lane to the head of the row, the rest shifting right, and brings
 *  it into view. Two presses are two asks — the member scrolled away between them. */
test("the rail's tile carries its lane to the head of the row and brings it into view", async () => {
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
    expect(location.hash).toBe(homeHash({ opens: [SECOND_ID, AGENT_ID] }));
    await waitFor(() => expect(laneNames()).toEqual(["Second", "Assistant"]));

    await userEvent.click(within(rail).getByRole("button", { name: "Second" }));

    expect(scrolled).toEqual(["Second", "Second"]);
    expect(location.hash).toBe(homeHash({ opens: [SECOND_ID, AGENT_ID] }));
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

    expect(location.hash).toBe(homeHash({ opens: [SECOND_ID, AGENT_ID] }));
    await waitFor(() => expect(laneNames()).toEqual(["Second", "Assistant"]));
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

test("the picker lane offers the workspace's apps and the member's history under one head", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([AGENT_ID]);

  const picker = await openPicker();
  expect(laneMark(picker)).toBeNull();

  const apps = within(picker).getByRole("heading", { name: "Apps", level: 3 });
  const history = within(picker).getByRole("heading", { name: "History", level: 3 });
  expect(
    within(apps.parentElement!)
      .getAllByRole("button")
      .map((row) => row.textContent),
  ).toEqual(["Assistant", "Second"]);
  expect(
    within(history.parentElement!)
      .getAllByRole("button")
      .map((row) => row.textContent),
  ).toEqual([CHAT_ROW.title]);
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

test("picking a conversation off the picker's history opens it as its own lane", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([AGENT_ID]);

  const picker = await openPicker();
  await userEvent.click(within(picker).getByRole("button", { name: CHAT_ROW.title }));

  await waitFor(() =>
    expect(location.hash).toBe(homeHash({ opens: [CONVERSATION_LANE, AGENT_ID] })),
  );
  expect(laneNames()).toEqual([CHAT_ROW.title, "Assistant"]);
});

test("picking a conversation already standing closes the picker instead of doubling it", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([AGENT_ID, CONVERSATION_LANE]);

  await screen.findByRole("region", { name: CHAT_ROW.title });
  const picker = await openPicker();
  await userEvent.click(within(picker).getByRole("button", { name: CHAT_ROW.title }));

  await waitFor(() =>
    expect(location.hash).toBe(homeHash({ opens: [AGENT_ID, CONVERSATION_LANE] })),
  );
  expect(laneNames()).toEqual(["Assistant", CHAT_ROW.title]);
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
