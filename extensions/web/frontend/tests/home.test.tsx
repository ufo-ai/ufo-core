import { act, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { MARK_HELD } from "@/components/MinimalSidebar";
import { LANE_NEXT, LANE_PRIOR } from "@/kernel/slots";
import {
  holdTurn,
  moveTurnHold,
  releaseTurn,
  statusDot,
  useAppStatus,
  type AgentStatus,
} from "@/lib/appStatusStore";
import { readDraft } from "@/lib/drafts";
import { heldChatHidden, holdChatHidden } from "@/lib/rail";
import { setPendingAsk } from "@/lib/pendingAsk";
import {
  chatHash,
  HOME_NEW_LANE,
  homeConversationLane,
  homeHash,
  homeLaneAgent,
  mintHomeLane,
  newChatHash,
} from "@/lib/route";
import { TRACK_MAX_SLOTS } from "@/lib/tracks";

import {
  AGENT,
  AGENT_ID,
  CHAT_APP,
  CHAT_APP_ID,
  CHAT_ROW,
  chatsOnWire,
  conversationObject,
  CONVO_ID,
  json,
  MEMBER,
  SECOND,
  SECOND_ID,
  TURN_ID,
  useStreamFake,
  wire,
} from "./harness";

const OTHER_CONVO_ID = "66666666-6666-4666-8666-666666666666";
const PRIVATE_CONVO_ID = "77777777-7777-4777-8777-777777777777";
const FOUNDED_CONVO_ID = "99999999-9999-4999-8999-999999999999";

const AGENT_INSTANCE = mintHomeLane(AGENT_ID, [AGENT_ID]);
const CONVERSATION_LANE = homeConversationLane(CONVO_ID);

/** A row at the cap, the two apps standing turn and turn about, each lane its own instance. */
const FULL_ROW = Array.from({ length: TRACK_MAX_SLOTS }).reduce<string[]>(
  (row, _, at) => [...row, mintHomeLane(at % 2 ? SECOND_ID : AGENT_ID, row)],
  [],
);

/** The sends that founded a conversation, off the wire's own record of what was asked. */
const foundingSends = (calls: string[]): string[] =>
  calls.filter((url) => url.includes("/chat?conversation=new"));

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

test("a fresh home opens one chat lane and keeps the feature apps in the picker", async () => {
  const metrics = { ...SECOND, name: "metrics", app: "metrics" };
  const meetings = {
    ...SECOND,
    id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    name: "meetings",
    app: "meetings",
  };
  const code = {
    ...SECOND,
    id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    name: "code",
    app: "code",
  };
  wire(chatsOnWire([]));
  render(<App agents={[metrics, meetings, code, CHAT_APP]} member={MEMBER} onAgents={() => {}} />);

  await waitFor(() => expect(location.hash).toBe(homeHash({ opens: [CHAT_APP_ID] })));
  expect(laneNames()).toEqual(["Chat"]);

  const picker = await openPicker();
  expect(within(picker).getByRole("button", { name: "Metrics" })).toBeTruthy();
  expect(within(picker).getByRole("button", { name: "Meetings" })).toBeTruthy();
  expect(within(picker).getByRole("button", { name: "Code" })).toBeTruthy();
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

/** The rail marks the lane the member is standing in, and the bracket walk is what moves it: the
 *  tile the press lands on is the one the box stands under. The mark is the rail's whole answer to
 *  "which of these am I in", so a row nobody has stood in yet carries none. */
test("the rail marks the lane the member is standing in, and the walk moves the mark", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([AGENT_ID, SECOND_ID]);
  await waitFor(() => expect(laneNames()).toEqual(["Assistant", "Second"]));
  const rail = screen.getByRole("navigation", { name: "Tabs" });
  const marked = () =>
    within(rail)
      .getAllByRole("button")
      .filter((tile) => tile.getAttribute("aria-current") === "true")
      .map((tile) => tile.getAttribute("aria-label"));

  expect(marked()).toEqual([]);

  fireEvent.keyDown(document, { key: LANE_NEXT });

  await waitFor(() => expect(marked()).toEqual(["Second"]));

  fireEvent.keyDown(document, { key: LANE_PRIOR });

  await waitFor(() => expect(marked()).toEqual(["Assistant"]));
});

/** The mark names a lane on home's row, so it goes when that row does: a tile still marked on the
 *  workspace screen names a lane standing nowhere on it. */
test("the rail drops its mark when the member leaves home", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([AGENT_ID, SECOND_ID]);
  await waitFor(() => expect(laneNames()).toEqual(["Assistant", "Second"]));
  const rail = screen.getByRole("navigation", { name: "Tabs" });
  const marked = () =>
    within(rail)
      .getAllByRole("button")
      .filter((tile) => tile.getAttribute("aria-current") === "true").length;

  fireEvent.keyDown(document, { key: LANE_NEXT });

  await waitFor(() => expect(marked()).toBe(1));

  await userEvent.click(within(rail).getByRole("button", { name: "Workspace" }));
  await waitFor(() => expect(laneNames()).toEqual([]));

  expect(marked()).toBe(0);
});

/** The mark answers a question the member asked by walking the row, so it stands long enough to be
 *  read and then leaves the rail alone. The tile stays the current one — what fades is the box, not
 *  the answer. */
test("the rail's mark fades once the member has had time to read it", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([AGENT_ID, SECOND_ID]);
  await waitFor(() => expect(laneNames()).toEqual(["Assistant", "Second"]));
  const rail = screen.getByRole("navigation", { name: "Tabs" });
  const mark = () => rail.querySelector("li[aria-hidden]") as HTMLElement;

  expect(mark().className).toContain("opacity-0");

  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  try {
    await act(async () => void fireEvent.keyDown(document, { key: LANE_NEXT }));

    expect(mark().className).not.toContain("opacity-0");

    act(() => void vi.advanceTimersByTime(MARK_HELD));

    expect(mark().className).toContain("opacity-0");
    expect(
      within(rail).getByRole("button", { name: "Second" }).getAttribute("aria-current"),
    ).toBe("true");
  } finally {
    vi.useRealTimers();
  }
});

/** A rail press is the other way a lane becomes the one the member is in, and the mark follows it
 *  even though the press took focus off the row. */
test("the rail marks the lane its own tile was pressed for", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([AGENT_ID, SECOND_ID]);
  await waitFor(() => expect(laneNames()).toEqual(["Assistant", "Second"]));
  const rail = screen.getByRole("navigation", { name: "Tabs" });

  await userEvent.click(within(rail).getByRole("button", { name: "Second" }));

  await waitFor(() =>
    expect(
      within(rail).getByRole("button", { name: "Second" }).getAttribute("aria-current"),
    ).toBe("true"),
  );
});

/** The row may be wider than the screen, and the rail's tile is how a lane that scrolled off it is
 *  reached: the press brings that lane into view and leaves the row in the order the member arranged
 *  it. Two presses are two asks — the member scrolled away between them. Every scroll the press
 *  causes is written down, the rail's own mark included, so a stray one cannot hide behind a
 *  narrower spy: the lane comes into view, then the mark does, and the second press moves the mark
 *  nowhere because the member has not left the lane it already stands under. */
test("the rail's tile brings its lane into view and leaves the row where it stood", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  const scrolled: string[] = [];
  const scrolls = vi
    .spyOn(Element.prototype, "scrollIntoView")
    .mockImplementation(function (this: Element) {
      const named = this.getAttribute("aria-label");
      scrolled.push(this.tagName + (named === null ? "" : ":" + named));
    });
  try {
    drawHome([AGENT_ID, SECOND_ID]);
    await waitFor(() => expect(laneNames()).toEqual(["Assistant", "Second"]));
    expect(scrolled).toEqual([]);
    const rail = screen.getByRole("navigation", { name: "Tabs" });

    await userEvent.click(within(rail).getByRole("button", { name: "Second" }));

    expect(scrolled).toEqual(["SECTION:Second", "LI"]);
    expect(location.hash).toBe(homeHash({ opens: [AGENT_ID, SECOND_ID] }));
    expect(laneNames()).toEqual(["Assistant", "Second"]);

    await userEvent.click(within(rail).getByRole("button", { name: "Second" }));

    expect(scrolled).toEqual(["SECTION:Second", "LI", "SECTION:Second"]);
    expect(location.hash).toBe(homeHash({ opens: [AGENT_ID, SECOND_ID] }));
  } finally {
    scrolls.mockRestore();
  }
});

/** The press places home and the lane it names lands with the row, a commit after home mounts. The
 *  mark stands on the tile that was pressed all the same: the ask waits for its lane rather than
 *  going out under a row that has not stood it yet, or the member is left on a rail marking
 *  nothing. */
test("the rail's tile from another screen lands home with that lane in view and marked", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  const scrolled: string[] = [];
  const scrolls = vi
    .spyOn(Element.prototype, "scrollIntoView")
    .mockImplementation(function (this: Element) {
      const named = this.getAttribute("aria-label");
      scrolled.push(this.tagName + (named === null ? "" : ":" + named));
    });
  try {
    drawHome([AGENT_ID, SECOND_ID]);
    await waitFor(() => expect(laneNames()).toEqual(["Assistant", "Second"]));
    await userEvent.click(await screen.findByRole("button", { name: "Workspace" }));
    await waitFor(() => expect(laneNames()).toEqual([]));
    const rail = screen.getByRole("navigation", { name: "Tabs" });
    const marked = () =>
      within(rail)
        .getAllByRole("button")
        .filter((tile) => tile.getAttribute("aria-current") === "true")
        .map((tile) => tile.getAttribute("aria-label"));

    await userEvent.click(within(rail).getByRole("button", { name: "Second" }));

    expect(location.hash).toBe(homeHash({ opens: [AGENT_ID, SECOND_ID] }));
    await waitFor(() => expect(laneNames()).toEqual(["Assistant", "Second"]));
    await waitFor(() => expect(marked()).toEqual(["Second"]));
    expect(scrolled).toEqual(["SECTION:Second", "LI"]);
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

/** The picker offers the apps and, under them, the conversations the member has had — one list,
 *  one order, wherever they are read. A row takes the lane over as that conversation's lane. The
 *  connectors screen closes the app list: the portal draws it rather than an app, and a member
 *  reaches it the way they reach an app. */
test("the picker lane offers the workspace's apps and the member's history", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([AGENT_ID]);

  const picker = await openPicker();
  expect(laneMark(picker)).toBeNull();

  const apps = within(picker).getByRole("heading", { name: "Apps", level: 3 });
  expect(
    within(apps.parentElement!)
      .getAllByRole("button")
      .map((row) => row.textContent),
  ).toEqual(["Assistant", "Second", "ConnectorsConnect the accounts your apps work in."]);

  const history = within(picker).getByRole("heading", { name: "History", level: 3 });
  const rows = within(history.closest("section")!).getAllByRole("listitem");
  expect(rows.map((row) => row.textContent)).toEqual([CHAT_ROW.title]);
  await userEvent.click(within(rows[0]).getByRole("button"));
  expect(laneNames()).toEqual([CHAT_ROW.title, "Assistant"]);
});

/** The picker's history is the chat lane's own list, drawn through `chatRuns` and then flattened: a
 *  surface the member put away stays put away, and every row that is left stands in one list under
 *  the one heading, with no run drawn apart inside it. */
test("the picker's history hides put-away surfaces and draws one flat list", async () => {
  wire(
    chatsOnWire([
      CHAT_ROW,
      { ...CHAT_ROW, conversation_id: OTHER_CONVO_ID, mine: false, surface: "ufo" },
      { ...CHAT_ROW, conversation_id: PRIVATE_CONVO_ID, surface: "slack", title: "Hidden thread" },
    ]),
  );
  holdChatHidden(["slack"]);
  drawHome([AGENT_ID]);

  const picker = await openPicker();
  const chats = within(picker)
    .getByRole("heading", { name: "History", level: 3 })
    .closest("section")!;
  // The Slack row is the surface the member put away, and a portal row is never hidden: the
  // hidden one must be the only row absent.
  expect(within(chats).queryByText("Hidden thread")).toBeNull();
  expect(within(chats).getAllByText(CHAT_ROW.title)).toHaveLength(2);
  expect(within(chats).queryByRole("heading", { level: 4 })).toBeNull();
  expect(within(chats).getAllByRole("list")).toHaveLength(1);
});

/** The picker narrows its history the way the chat lane's own history does: the same menu, over the
 *  same held choices, so a surface put away here is put away wherever it is read. */
test("the picker's history offers the history menu", async () => {
  wire(
    chatsOnWire([
      CHAT_ROW,
      { ...CHAT_ROW, conversation_id: PRIVATE_CONVO_ID, surface: "slack", title: "Hidden thread" },
    ]),
  );
  drawHome([AGENT_ID]);

  const picker = await openPicker();
  const chats = within(picker)
    .getByRole("heading", { name: "History", level: 3 })
    .closest("section")!;
  expect(within(chats).getByText("Hidden thread")).toBeTruthy();

  await userEvent.click(within(chats).getByRole("button", { name: "History options" }));
  await userEvent.click(await screen.findByRole("menuitemcheckbox", { name: "Slack" }));

  await waitFor(() => expect(within(chats).queryByText("Hidden thread")).toBeNull());
  expect(heldChatHidden()).toEqual(["slack"]);
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

test("a new chat tab opens its history at the top after the rows load", async () => {
  let answer!: (response: Response) => void;
  const scroll = vi.spyOn(Element.prototype, "scrollTo");
  const rows = Array.from({ length: 30 }, (_, at) => ({
    ...CHAT_ROW,
    conversation_id: `00000000-0000-4000-8000-${String(at).padStart(12, "0")}`,
    title: `History row ${String(at + 1).padStart(2, "0")}`,
  }));
  wire({
    "/objects/conversation$": () =>
      new Promise<Response>((resolve) => {
        answer = resolve;
      }),
  });
  drawHome([AGENT_ID]);

  const lane = await screen.findByRole("region", { name: "Assistant" });
  await within(lane).findByRole("heading", { name: "History", level: 3 });
  scroll.mockClear();

  act(() => answer(json({ objects: rows.map(conversationObject) })));
  const first = await within(lane).findByText("History row 01");
  expect(within(lane).getAllByText(/History row \d{2}/)).toHaveLength(30);
  const history = first.closest("section")?.parentElement;
  expect(history?.className).toContain("overflow-y-auto");
  expect(within(lane).queryByTestId("log")).toBeNull();
  await waitFor(() => {
    const call = (scroll.mock.instances as unknown[]).findIndex((node) => node === history);
    expect(call).not.toBe(-1);
    expect(scroll.mock.calls[call][0]).toMatchObject({ top: 0 });
  });
});

/** The stamp on a row is the moment its conversation last moved, read as the distance from now
 *  while that is what says a row is recent and as the day itself once it is not — a few characters
 *  either way, so a narrow lane spends its width on the title rather than on a full date per row. */
test("a history row stamps when its conversation last moved", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([AGENT_ID]);

  const lane = await screen.findByRole("region", { name: "Assistant" });
  await within(lane).findByRole("heading", { name: "History", level: 3 });

  const stamp = within(lane).getByText("Aug 1 2026");
  expect(stamp.getAttribute("datetime")).toBe(CHAT_ROW.last_at);
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

/** The act on a lane's band opens the very list the chat lane opens on: the same heading, the same
 *  narrowings behind their glyph, the same day runs, and the entry the next conversation starts in
 *  holding the foot. */
test("the history a lane's band opens is the one the chat lane opens on, over a new chat entry", async () => {
  const terminal = {
    ...CHAT_ROW,
    conversation_id: OTHER_CONVO_ID,
    title: "Ship the ledger",
    surface: "ufo",
    last_at: new Date().toISOString(),
  };
  wire(chatsOnWire([{ ...CHAT_ROW, last_at: new Date().toISOString() }, terminal]));
  drawHome([CONVERSATION_LANE]);

  await userEvent.click(await screen.findByRole("button", { name: "History for Assistant" }));
  const lane = await screen.findByRole("region", { name: "History" });

  expect(within(lane).getByRole("heading", { name: "History", level: 3 })).toBeTruthy();
  expect(within(lane).getByRole("button", { name: "History options" })).toBeTruthy();
  expect(within(lane).getByRole("heading", { name: "Today", level: 4 })).toBeTruthy();
  expect(within(lane).getByText(new RegExp(terminal.title))).toBeTruthy();
  expect((within(lane).getByLabelText("Ask UFO") as HTMLTextAreaElement).placeholder).toBe(
    "Start new chat…",
  );
});

/** Two lanes of one app are two conversations, and the entry under a history is that lane's own. The
 *  send that founds one leaves the lane beside it exactly as it stood: its box still sends, and the
 *  words the member typed there are still there to send. On one key between them the founding send
 *  leaves that key holding the forwarding record, so the second box has nothing to send into, and it
 *  clears the draft the member left in it. */
test("founding under one lane's history leaves the lane beside it sending, with its own words", async () => {
  let answered = 0;
  const { calls } = wire({
    ...chatsOnWire([CHAT_ROW]),
    "/slots": () => json({ slots: [] }),
    "/transcript": () => json({ messages: [] }),
    "/chat": () => {
      answered += 1;
      return json({
        turn_id: TURN_ID,
        conversation_id: answered === 1 ? OTHER_CONVO_ID : FOUNDED_CONVO_ID,
        title: "Ship the ledger",
      });
    },
  });
  drawHome([AGENT_ID, AGENT_INSTANCE]);

  await waitFor(() => expect(laneNames()).toEqual(["Assistant", "Assistant"]));
  const [first, second] = standingLanes();
  await userEvent.click(within(first).getByRole("button", { name: "History for Assistant" }));
  await userEvent.click(within(second).getByRole("button", { name: "History for Assistant" }));

  await userEvent.type(within(second).getByLabelText("Ask UFO"), "words for the lane beside it");
  await userEvent.type(within(first).getByLabelText("Ask UFO"), "found this one");
  await userEvent.click(within(first).getByRole("button", { name: "Send" }));

  await waitFor(() =>
    expect(location.hash).toBe(
      homeHash({ opens: [homeConversationLane(OTHER_CONVO_ID), AGENT_INSTANCE] }),
    ),
  );
  const beside = await screen.findByRole("region", { name: "History" });
  expect(readDraft(MEMBER.id + "/history:" + AGENT_INSTANCE)).toBe("words for the lane beside it");

  const send = within(beside).getByRole("button", { name: "Send" }) as HTMLButtonElement;
  expect(send.disabled).toBe(false);
  await userEvent.click(send);

  await waitFor(() => expect(foundingSends(calls)).toHaveLength(2));
});

/** The palette's ask names the one composer it is for, and a lane turned to its history is why: it
 *  stands a founding box for the same agent, and on the chat screen's own key it would take the ask
 *  the palette set on its way to that screen — the words said into a screen the member has left, and
 *  the conversation they open standing in no lane. The lane's box founds on its own key, so the ask
 *  is left standing until the chat screen reads it. */
test("an ask the palette means for the chat screen is left standing by a lane's history", async () => {
  const { calls } = wire({
    ...chatsOnWire([CHAT_ROW]),
    "/slots": () => json({ slots: [] }),
    "/transcript": () => json({ messages: [] }),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: OTHER_CONVO_ID, title: "deploys" }),
  });
  drawHome([AGENT_ID]);

  await userEvent.click(await screen.findByRole("button", { name: "History for Assistant" }));
  const lane = await screen.findByRole("region", { name: "History" });
  act(() => setPendingAsk(AGENT_ID, "deploy the ledger", true, "new:" + AGENT_ID));

  expect((within(lane).getByLabelText("Ask UFO") as HTMLTextAreaElement).value).toBe("");
  expect(foundingSends(calls)).toHaveLength(0);

  location.hash = newChatHash(AGENT_ID);
  window.dispatchEvent(new HashChangeEvent("hashchange"));

  await waitFor(() => expect(location.hash).toBe(chatHash(OTHER_CONVO_ID)));
  expect(foundingSends(calls)).toHaveLength(1);
  expect(foundingSends(calls)[0]).toContain("/agents/" + AGENT_ID + "/chat");
});

/** An ask keyed to the agent alone — the setup screen's acts and a page's compose both hand one to
 *  the agent's new chat and route there — is meant for that screen's own composer. A lane's history
 *  stands a founding box for the same agent, and a take there would delete the ask while the
 *  navigation tears the lane down: the words reach no box the member can send. */
test("an ask keyed to the agent alone is left standing by a lane's history", async () => {
  const { calls } = wire({
    ...chatsOnWire([CHAT_ROW]),
    "/slots": () => json({ slots: [] }),
    "/transcript": () => json({ messages: [] }),
  });
  drawHome([AGENT_ID]);

  await userEvent.click(await screen.findByRole("button", { name: "History for Assistant" }));
  const lane = await screen.findByRole("region", { name: "History" });
  act(() => setPendingAsk(AGENT_ID, "Build it.", false));
  expect((within(lane).getByLabelText("Ask UFO") as HTMLTextAreaElement).value).toBe("");

  location.hash = newChatHash(AGENT_ID);
  window.dispatchEvent(new HashChangeEvent("hashchange"));

  await waitFor(() =>
    expect((screen.getByLabelText("Ask UFO") as HTMLTextAreaElement).value).toBe("Build it."),
  );
  expect(foundingSends(calls)).toHaveLength(0);
});

/** A founding send that fails is read where it was said: the words stand in the log with the fault
 *  under them, and the box is live to send again — never a box that cleared and shows nothing. */
test("a founding send that fails under a lane's history keeps the words and states the fault", async () => {
  wire({
    ...chatsOnWire([CHAT_ROW]),
    "/slots": () => json({ slots: [] }),
    "/transcript": () => json({ messages: [] }),
    "/chat": () => new Response(null, { status: 500 }),
  });
  drawHome([AGENT_ID]);

  await userEvent.click(await screen.findByRole("button", { name: "History for Assistant" }));
  const lane = await screen.findByRole("region", { name: "History" });
  await userEvent.type(within(lane).getByLabelText("Ask UFO"), "found the ledger");
  await userEvent.click(within(lane).getByRole("button", { name: "Send" }));

  expect(await within(lane).findByText("found the ledger")).toBeTruthy();
  expect(await within(lane).findByText("Error 500 — try again.")).toBeTruthy();
  const send = within(lane).getByRole("button", { name: "Send" }) as HTMLButtonElement;
  expect(send.disabled).toBe(false);
});

/** The lane's own box may be founding on the lane key when the member turns to the history. The
 *  landing moves that key to the conversation and leaves the forwarding record behind it; the
 *  history's box founds on a key of its own, so it is still standing and still sends. */
test("a founding send that lands while the history stands leaves the history's own box sending", async () => {
  let release: (landed: Response) => void = () => {};
  let sends = 0;
  wire({
    ...chatsOnWire([CHAT_ROW]),
    "/slots": () => json({ slots: [] }),
    "/transcript": () => json({ messages: [] }),
    "/chat": () => {
      sends += 1;
      if (sends === 1) return new Promise<Response>((resolve) => (release = resolve));
      return json({ turn_id: TURN_ID, conversation_id: FOUNDED_CONVO_ID, title: "the next one" });
    },
  });
  drawHome([AGENT_ID]);

  await userEvent.type(await screen.findByLabelText("Ask UFO"), "first");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await userEvent.click(screen.getByRole("button", { name: "History for Assistant" }));
  const lane = await screen.findByRole("region", { name: "History" });
  act(() =>
    release(json({ turn_id: TURN_ID, conversation_id: OTHER_CONVO_ID, title: "Ship the ledger" })),
  );
  await within(lane).findByText(/Ship the ledger/);

  await userEvent.type(within(lane).getByLabelText("Ask UFO"), "second");
  await userEvent.click(within(lane).getByRole("button", { name: "Send" }));

  await waitFor(() =>
    expect(location.hash).toBe(homeHash({ opens: [homeConversationLane(FOUNDED_CONVO_ID)] })),
  );
});

/** Two founding sends can be in flight at once, and each landing writes the track as the address
 *  holds it at the landing, never as it stood when the send left: the second landing keeps the
 *  conversation the first put on the track. */
test("two founding sends racing under two histories land as two conversations", async () => {
  const releases: ((landed: Response) => void)[] = [];
  wire({
    ...chatsOnWire([CHAT_ROW]),
    "/slots": () => json({ slots: [] }),
    "/transcript": () => json({ messages: [] }),
    "/chat": () => new Promise<Response>((resolve) => releases.push(resolve)),
  });
  drawHome([AGENT_ID, AGENT_INSTANCE]);

  await waitFor(() => expect(laneNames()).toEqual(["Assistant", "Assistant"]));
  const [first, second] = standingLanes();
  await userEvent.click(within(first).getByRole("button", { name: "History for Assistant" }));
  await userEvent.click(within(second).getByRole("button", { name: "History for Assistant" }));
  await userEvent.type(within(first).getByLabelText("Ask UFO"), "first");
  await userEvent.click(within(first).getByRole("button", { name: "Send" }));
  await userEvent.type(within(second).getByLabelText("Ask UFO"), "second");
  await userEvent.click(within(second).getByRole("button", { name: "Send" }));

  await waitFor(() => expect(releases).toHaveLength(2));
  act(() => releases[0](json({ turn_id: TURN_ID, conversation_id: OTHER_CONVO_ID, title: "one" })));
  await waitFor(() =>
    expect(location.hash).toBe(
      homeHash({ opens: [homeConversationLane(OTHER_CONVO_ID), AGENT_INSTANCE] }),
    ),
  );
  act(() => releases[1](json({ turn_id: TURN_ID, conversation_id: FOUNDED_CONVO_ID, title: "two" })));
  await waitFor(() =>
    expect(location.hash).toBe(
      homeHash({
        opens: [homeConversationLane(OTHER_CONVO_ID), homeConversationLane(FOUNDED_CONVO_ID)],
      }),
    ),
  );
});

/** The hand-off that swaps the lane clears what the founding left behind — the forwarding record on
 *  the history's own key, and the lane key the chat box founds on — so the next lane this app
 *  stands up opens a new chat rather than the conversation the member just left. */
test("the next lane this app stands up opens a new chat, not the conversation the history founded", async () => {
  wire({
    ...chatsOnWire([CHAT_ROW]),
    "/slots": () => json({ slots: [] }),
    "/transcript": () => json({ messages: [] }),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: FOUNDED_CONVO_ID, title: "next" }),
  });
  drawHome([AGENT_ID]);

  await userEvent.click(await screen.findByRole("button", { name: "History for Assistant" }));
  const lane = await screen.findByRole("region", { name: "History" });
  await userEvent.type(within(lane).getByLabelText("Ask UFO"), "found it");
  await userEvent.click(within(lane).getByRole("button", { name: "Send" }));
  await waitFor(() =>
    expect(location.hash).toBe(homeHash({ opens: [homeConversationLane(FOUNDED_CONVO_ID)] })),
  );

  location.hash = homeHash({ opens: [AGENT_ID] });
  window.dispatchEvent(new HashChangeEvent("hashchange"));

  await waitFor(() => expect(laneNames()).toEqual(["Assistant"]));
  const box = (await screen.findByLabelText("Ask UFO")) as HTMLTextAreaElement;
  expect(box.placeholder).toBe("Start new chat…");
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
  expect(within(lane).getByRole("heading", { name: "Other members", level: 4 })).toBeTruthy();
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

/** The dot stands outside the mark's own box, and the track the tiles scroll in clips both axes: a
 *  box that scrolls one way clips the other. So the track carries a gutter as wide as the overhang
 *  and pulls back out over the rail's padding, leaving the marks in the column they stood in. Held
 *  to the rail's own width instead, the clip edge cuts every dot in half. */
test("the rail's tile track keeps the dot's overhang inside the scroll box", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([AGENT_ID]);

  const rail = await screen.findByRole("navigation", { name: "Tabs" });
  const track = rail.querySelector("ul");

  expect(track?.className).toContain("overflow-y-auto");
  expect(track?.className).toContain("px-2xs");
  expect(track?.className).toContain("-mx-2xs");
  expect(track?.className).not.toContain("w-full");
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
