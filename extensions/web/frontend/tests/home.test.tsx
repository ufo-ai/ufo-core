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
  mintHomeLane,
  newChatHash,
} from "@/lib/route";

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

const foundingSends = (calls: string[]): string[] =>
  calls.filter((url) => url.includes("/chat?conversation=new"));

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

const openPicker = async (beside: string[] = []): Promise<HTMLElement> => {
  location.hash = homeHash({ opens: [HOME_NEW_LANE, ...beside] });
  window.dispatchEvent(new HashChangeEvent("hashchange"));
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

  const picker = await openPicker([CHAT_APP_ID]);
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

test("the picker lane offers the workspace's apps and the member's history", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([AGENT_ID]);

  const picker = await openPicker([AGENT_ID]);
  expect(laneMark(picker)).toBeNull();

  const apps = within(picker).getByRole("heading", { name: "Apps", level: 3 });
  expect(
    within(apps.parentElement!)
      .getAllByRole("button")
      .map((row) => row.textContent),
  ).toEqual(["Assistant", "Second", "ConnectorsConnect the accounts your apps work in."]);

  const history = within(picker).getByRole("heading", { name: "History", level: 3 });
  const chats = history.closest("section")!;
  const rows = within(chats)
    .getAllByRole("button")
    .filter((row) => row.textContent !== "");
  expect(rows.map((row) => row.textContent)).toEqual([CHAT_ROW.title]);
  await userEvent.click(rows[0]);
  expect(laneNames()).toEqual([CHAT_ROW.title, "Assistant"]);
});

test("the picker's history hides put-away surfaces and names no run under recency", async () => {
  wire(
    chatsOnWire([
      CHAT_ROW,
      { ...CHAT_ROW, conversation_id: OTHER_CONVO_ID, mine: false, surface: "ufo" },
      { ...CHAT_ROW, conversation_id: PRIVATE_CONVO_ID, surface: "slack", title: "Hidden thread" },
    ]),
  );
  holdChatHidden(["slack"]);
  drawHome([AGENT_ID]);

  const picker = await openPicker([AGENT_ID]);
  const chats = within(picker)
    .getByRole("heading", { name: "History", level: 3 })
    .closest("section")!;
  expect(within(chats).queryByText(new RegExp("Hidden thread"))).toBeNull();
  expect(within(chats).getAllByText(new RegExp(CHAT_ROW.title))).toHaveLength(2);
  expect(within(chats).queryByRole("heading", { level: 4 })).toBeNull();
  expect(within(chats).getByText(new RegExp("Terminal"))).toBeTruthy();
  expect(within(chats).queryAllByRole("list")).toHaveLength(0);
});

test("the picker's history names the app over each run under the app ladder", async () => {
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

  const picker = await openPicker();
  const chats = within(picker)
    .getByRole("heading", { name: "History", level: 3 })
    .closest("section")!;

  await userEvent.click(within(chats).getByRole("button", { name: "History options" }));
  await userEvent.click(await screen.findByRole("menuitemradio", { name: "App" }));

  await waitFor(() =>
    expect(within(chats).getByRole("heading", { name: "Assistant", level: 4 })).toBeTruthy(),
  );
  expect(within(chats).getByRole("heading", { name: "Second", level: 4 })).toBeTruthy();
  expect(localStorage.getItem("chat-ladder")).toBe("app");

  await userEvent.click(within(chats).getByRole("button", { name: "History options" }));
  await userEvent.click(await screen.findByRole("menuitemradio", { name: "Recency" }));

  await waitFor(() => expect(within(chats).queryByRole("heading", { level: 4 })).toBeNull());
  expect(within(chats).getByText(new RegExp(terminal.title))).toBeTruthy();
});

test("the picker's history offers the history menu", async () => {
  wire(
    chatsOnWire([
      CHAT_ROW,
      { ...CHAT_ROW, conversation_id: PRIVATE_CONVO_ID, surface: "slack", title: "Hidden thread" },
    ]),
  );
  drawHome([AGENT_ID]);

  const picker = await openPicker([AGENT_ID]);
  const chats = within(picker)
    .getByRole("heading", { name: "History", level: 3 })
    .closest("section")!;
  expect(within(chats).getByText(new RegExp("Hidden thread"))).toBeTruthy();

  await userEvent.click(within(chats).getByRole("button", { name: "History options" }));
  await userEvent.click(await screen.findByRole("menuitemcheckbox", { name: "Slack" }));

  await waitFor(() => expect(within(chats).queryByText(new RegExp("Hidden thread"))).toBeNull());
  expect(heldChatHidden()).toEqual(["slack"]);
});

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

test("a history row stamps when its conversation last moved", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([AGENT_ID]);

  const lane = await screen.findByRole("region", { name: "Assistant" });
  await within(lane).findByRole("heading", { name: "History", level: 3 });

  const stamp = within(lane).getByText("Aug 1 2026");
  expect(stamp.getAttribute("datetime")).toBe(CHAT_ROW.last_at);
});

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

  await userEvent.click(within(second).getByRole("button", { name: "History options" }));
  expect(
    (await screen.findByRole("menuitemcheckbox", { name: "Terminal" })).getAttribute("aria-checked"),
  ).toBe("false");
});

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

  const rows = [...lane.querySelectorAll("section")]
    .flatMap((run) => [...run.querySelectorAll("button")])
    .map((row) => row.textContent ?? "");
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

  const picker = await openPicker([AGENT_ID]);
  await userEvent.click(within(picker).getByRole("button", { name: "Second" }));

  await waitFor(() => expect(location.hash).toBe(homeHash({ opens: [SECOND_ID, AGENT_ID] })));
  expect(laneNames()).toEqual(["Second", "Assistant"]);
});

test("picking an app already standing mints a second instance in the picker lane's place", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([AGENT_ID]);

  const picker = await openPicker([AGENT_ID]);
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

/** A box that scrolls one way clips the other, so the track carries a gutter as wide as the dot's
 *  overhang and pulls back out over the rail's padding. */
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

test("the picker lane's band carries the New tab glyph", async () => {
  wire(chatsOnWire([CHAT_ROW]));
  drawHome([AGENT_ID]);

  const picker = await openPicker([AGENT_ID]);
  expect(picker.querySelector("[data-slot=header] svg.tabler-icon-plus")).not.toBeNull();
});

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
