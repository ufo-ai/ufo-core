import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { WORKING_STATUS_MS } from "@/lib/appStatusStore";
import { rowMoment } from "@/lib/moments";
import { heldRailShown, holdRailShown, stampIso } from "@/lib/rail";
import type { ConversationTurn } from "@/lib/types";

import { AGENT, AGENT_ID, atPhoneWidth, CHAT_APP, CHAT_APP_ID, CHAT_ROW, chatsOnWire, conversationObject, CONVO_ID, destination, json, MEMBER, objectIndex, SECOND, SECOND_ID, SITE_KIND, StreamFake, TASK_KIND, TRIGGER_KIND, TURN_ID, useStreamFake, wire } from "../../tests/harness";

beforeEach(() => {
  useStreamFake();
});

const PORTAL_ONLY = { terminal: false, slack: false, imessage: false, automations: true };
const EVERY_SURFACE = { terminal: true, slack: true, imessage: true, automations: true };

async function hoverCard(): Promise<HTMLElement> {
  return await waitFor(() => {
    const card = document.querySelector("[data-slot=hover-card-content]");
    if (card === null) throw new Error("no hover card");
    return card as HTMLElement;
  });
}

async function settle(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
    await Promise.resolve();
    await Promise.resolve();
  });
}

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
  await userEvent.click(await screen.findByRole("button", { name: "Recents options" }));
  await userEvent.click(await screen.findByRole("menuitemcheckbox", { name: label }));
  await userEvent.keyboard("{Escape}");
}

/** A comma list written for the surface-only option set carries no name for automations, and a
 *  read of it cannot tell a member's choice from the option's absence. */
test("a filter choice held before automations were filterable hides no automation", async () => {
  localStorage.setItem("rail-shown", "terminal,slack");
  const run = { ...CHAT_ROW, conversation_id: SECOND_ID, title: "Nightly digest", automation_kind: "scheduled_task", automation_name: "nightly-digest", automation_title: "Nightly digest" };
  wire({ ...chatsOnWire([CHAT_ROW, run]) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByRole("button", { name: /Nightly digest/ })).toBeTruthy();
  expect(heldRailShown().automations).toBe(true);
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
  expect(within(rail).getByRole("button", { name: "Recents options" })).toBeTruthy();

  await userEvent.click(await within(rail).findByRole("button", { name: /Pick one thread/ }));

  expect(location.hash).toBe("#/c/" + CONVO_ID);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

test("a tick leaves the filter open, so both surfaces are named in one visit", async () => {
  holdRailShown(PORTAL_ONLY);
  wire({ ...chatsOnWire([CHAT_ROW, SLACK_CHAT, TERMINAL_CHAT]) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await composerFocused();

  await userEvent.click(await screen.findByRole("button", { name: "Recents options" }));
  await userEvent.click(await screen.findByRole("menuitemcheckbox", { name: "Slack" }));
  await userEvent.click(screen.getByRole("menuitemcheckbox", { name: "Terminal" }));

  expect(screen.getByRole("button", { name: "Recents options" }).getAttribute("aria-expanded")).toBe(
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

/** The marks stand beside the row's press rather than inside it, so a read of the press is the
 *  title alone and the marks are reached the way the acts menu is. */
function trail(railRow: HTMLElement): HTMLElement {
  return railRow.closest("li")!;
}

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
  expect(within(sidebar).getAllByRole("heading").map((head) => head.textContent)).toEqual([
    "Recents",
  ]);
});

test("a colleague's conversation stands on the Home table and never in the rail", async () => {
  const colleague = {
    ...CHAT_ROW,
    conversation_id: SECOND_ID,
    title: "Colleague thread",
    last_at: stampIso(new Date()),
    audience: "shared",
    mine: false,
    speaker: "sam@example.com",
  };
  wire({ ...chatsOnWire([CHAT_ROW, colleague]), "/transcript": () => json({ messages: [] }) });
  location.hash = "#/chats";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const home = await screen.findByRole("region", { name: "Home" });
  expect(await within(home).findByRole("row", { name: /Colleague thread/ })).toBeTruthy();
  const rail = within(screen.getByRole("navigation", { name: "Workspace" }));
  expect(await rail.findByRole("button", { name: /Pick one thread/ })).toBeTruthy();
  expect(rail.queryByRole("button", { name: /Colleague thread/ })).toBeNull();
  expect(rail.queryByRole("button", { name: /Other members/ })).toBeNull();
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
  let held: ConversationTurn[] = ["running", "idle"];
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
      url.includes("/agents/" + AGENT_ID + "/conversations/" + CONVO_ID + "/transcript"),
    ),
  ).toBe(true);
  expect(location.hash).toBe("#/c/" + CONVO_ID);
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
  expect(within(trail(railRow)).getByRole("img", { name: "Slack" })).toBeTruthy();
  fireEvent.focus(railRow);
  expect((await hoverCard()).textContent).toContain("DM");
});

test("a pinned chat stands under a band of its own, above the recents", async () => {
  const held = { ...CHAT_ROW, conversation_id: SECOND_ID, title: "Ship the plan", pinned: true };
  wire({ ...chatsOnWire([CHAT_ROW, held]) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const rail = within(await screen.findByRole("navigation", { name: "Workspace" }));
  const pinned = rail.getByRole("heading", { name: "Pinned" });
  const recents = rail.getByRole("heading", { name: "Recents" });
  const row = await screen.findByRole("button", { name: /Ship the plan/ });
  expect(pinned.compareDocumentPosition(row) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(row.compareDocumentPosition(recents) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(
    recents.compareDocumentPosition(screen.getByRole("button", { name: /Pick one thread/ })) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
});

test("either rail band folds its own rows away, and leaves the other standing", async () => {
  const held = { ...CHAT_ROW, conversation_id: SECOND_ID, title: "Ship the plan", pinned: true };
  wire({ ...chatsOnWire([CHAT_ROW, held]) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const rail = within(await screen.findByRole("navigation", { name: "Workspace" }));
  await screen.findByRole("button", { name: /Ship the plan/ });

  await userEvent.click(rail.getByRole("button", { name: "Pinned" }));

  expect(screen.queryByRole("button", { name: /Ship the plan/ })).toBeNull();
  expect(screen.getByRole("button", { name: /Pick one thread/ })).toBeTruthy();
  expect(rail.getByRole("button", { name: "Pinned" }).getAttribute("aria-expanded")).toBe("false");

  await userEvent.click(rail.getByRole("button", { name: "Recents" }));

  expect(screen.queryByRole("button", { name: /Pick one thread/ })).toBeNull();
  expect(localStorage.getItem("sections-shut")).toBe("Pinned\nRecents");

  await userEvent.click(rail.getByRole("button", { name: "Pinned" }));

  expect(screen.getByRole("button", { name: /Ship the plan/ })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Pick one thread/ })).toBeNull();
});

test("the rail names no pinned band where the member has pinned nothing", async () => {
  wire({ ...chatsOnWire([CHAT_ROW]) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const rail = within(await screen.findByRole("navigation", { name: "Workspace" }));
  await screen.findByRole("button", { name: /Pick one thread/ });
  expect(rail.queryByRole("heading", { name: "Pinned" })).toBeNull();
});

test("a rail row that shared a file draws the clip, and one that shared none draws no mark", async () => {
  const shared = { ...CHAT_ROW, conversation_id: SECOND_ID, title: "Quarter report", artifacts: true };
  wire({ ...chatsOnWire([CHAT_ROW, shared]) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const carrying = await screen.findByRole("button", { name: /Quarter report/ });
  expect(within(trail(carrying)).getByRole("img", { name: "Shared a file" })).toBeTruthy();
  const bare = await screen.findByRole("button", { name: /Pick one thread/ });
  expect(within(trail(bare)).queryByRole("img", { name: "Shared a file" })).toBeNull();
});

test("a cli row draws the terminal glyph and reads as Terminal, never as the surface's own name", async () => {
  const cli = { ...CHAT_ROW, surface: "ufo", surface_label: null, title: "Deploy the branch" };
  holdRailShown(EVERY_SURFACE);
  wire({ ...chatsOnWire([cli]) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const railRow = await screen.findByRole("button", { name: /Deploy the branch/ });
  expect(trail(railRow).querySelector(".tabler-icon-terminal-2")).not.toBeNull();
  fireEvent.focus(railRow);
  expect((await hoverCard()).textContent).toContain("Terminal");
});

test("the surface glyph is drawn at the sidebar's glyph size, not at the row's text size", async () => {
  const slack = { ...CHAT_ROW, surface: "slack", surface_label: "#ops", title: "Slack question" };
  holdRailShown(EVERY_SURFACE);
  wire({ ...chatsOnWire([slack]) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const railRow = await screen.findByRole("button", { name: /Slack question/ });
  const drawn = trail(railRow)
    .querySelector(".tabler-icon-brand-slack")!
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

test("a rail row opens its conversation, never the page of the agent holding it", async () => {
  location.hash = "#/";
  const chatted = { ...CHAT_ROW, agent_id: CHAT_APP_ID, agent_name: "chat" };
  wire({ ...chatsOnWire([chatted]), "/transcript": () => json({ messages: [] }) });
  render(<App agents={[AGENT, CHAT_APP]} member={MEMBER} onAgents={() => {}} />);

  const rail = within(screen.getByRole("navigation", { name: "Workspace" }));
  await userEvent.click(await rail.findByRole("button", { name: /Pick one thread/ }));

  expect(location.hash).toBe("#/c/" + CONVO_ID);
});

test("an app's own conversation opens the same way", async () => {
  location.hash = "#/";
  const radar = { ...SECOND, name: "radar", app: "radar" };
  const alert = { ...CHAT_ROW, agent_id: SECOND_ID, agent_name: "radar", title: "Deploy alert" };
  wire({ ...chatsOnWire([alert]), "/transcript": () => json({ messages: [] }) });
  render(<App agents={[AGENT, CHAT_APP, radar]} member={MEMBER} onAgents={() => {}} />);

  const rail = within(screen.getByRole("navigation", { name: "Workspace" }));
  await userEvent.click(await rail.findByRole("button", { name: /Deploy alert/ }));

  expect(location.hash).toBe("#/c/" + CONVO_ID);
});

test("the ask row starts a conversation with the main agent, whatever page it carries", async () => {
  location.hash = "#/";
  wire({});
  render(<App agents={[AGENT, CHAT_APP]} member={MEMBER} onAgents={() => {}} />);

  const rail = within(screen.getByRole("navigation", { name: "Workspace" }));
  await userEvent.click(rail.getAllByRole("button", { name: "New chat" })[0]);

  expect(location.hash).toBe("#/new/" + AGENT_ID);
});

test("the chord the row prints starts the conversation the row would", async () => {
  location.hash = "#/";
  wire({});
  render(<App agents={[AGENT, CHAT_APP]} member={MEMBER} onAgents={() => {}} />);

  const rail = within(screen.getByRole("navigation", { name: "Workspace" }));
  const row = rail.getAllByRole("button", { name: "New chat" })[0];
  expect(row.getAttribute("aria-keyshortcuts")).toBe("Meta+Shift+O");

  await userEvent.keyboard("{Meta>}{Shift>}O{/Shift}{/Meta}");

  expect(location.hash).toBe("#/new/" + AGENT_ID);
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
