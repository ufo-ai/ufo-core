import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { agentName } from "@/lib/agentName";
import {
  bumpChat,
  heldRailShown,
  holdRailShown,
  mergeChats,
  railGroups,
  type ChatRow,
} from "@/lib/rail";

import {
  AGENT,
  AGENT_ID,
  CHAT_ROW,
  CONVO_ID,
  MEMBER,
  NO_ARTIFACTS,
  SECOND,
  SECOND_ID,
  SETTINGS,
  TURN_ID,
  SITE_KIND,
  TASK_KIND,
  TRIGGER_KIND,
  json,
  objectIndex,
  openAgentRow,
  useStreamFake,
  wire,
} from "./harness";

beforeEach(() => {
  useStreamFake();
});

const NOW = new Date(2026, 7, 1, 12, 0, 0);

const NOT_SHARED = "This conversation is not shared with this account.";

const PORTAL_ONLY = { terminal: false, slack: false };
const EVERY_SURFACE = { terminal: true, slack: true };

function hoursAgo(hours: number): string {
  return new Date(NOW.getTime() - hours * 3_600_000).toISOString();
}

function row(id: string, last_at: string): ChatRow {
  return {
    conversation_id: id,
    agent_id: AGENT_ID,
    agent_name: "assistant",
    title: "chat " + id,
    last_at,
    surface: "web",
    surface_label: null,
    mine: true,
    speaker: null,
  };
}

function theirs(id: string, last_at: string, speaker: string): ChatRow {
  return { ...row(id, last_at), mine: false, speaker };
}

test("chats group by recency in rail order and empty groups are absent", () => {
  const grouped = railGroups(
    [
      row("a", hoursAgo(3)),
      row("b", hoursAgo(20)),
      row("c", hoursAgo(4 * 24)),
      row("d", hoursAgo(20 * 24)),
      row("e", hoursAgo(90 * 24)),
    ],
    "recency",
    PORTAL_ONLY,
    NOW,
  );
  expect(grouped.map((group) => group.label)).toEqual([
    "Today",
    "Yesterday",
    "Previous 7 days",
    "Previous 30 days",
    "Older",
  ]);
  expect(grouped.map((group) => group.rows.map((entry) => entry.conversation_id))).toEqual([
    ["a"],
    ["b"],
    ["c"],
    ["d"],
    ["e"],
  ]);
  expect(railGroups([row("a", hoursAgo(4))], "recency", PORTAL_ONLY, NOW)).toHaveLength(1);
});

test("agent sort groups rows under their agent and keeps recency within each group", () => {
  const grouped = railGroups(
    [
      row("a", hoursAgo(1)),
      { ...row("b", hoursAgo(2)), agent_name: "support" },
      row("c", hoursAgo(3)),
    ],
    "agent",
    PORTAL_ONLY,
    NOW,
  );
  expect(grouped.map((group) => group.label)).toEqual(["Assistant", "Support"]);
  expect(grouped.map((group) => group.rows.map((entry) => entry.conversation_id))).toEqual([
    ["a", "c"],
    ["b"],
  ]);
});

test("everyone else's conversations are one group at the foot, under either sort", () => {
  const rows = [
    row("a", hoursAgo(1)),
    theirs("t1", hoursAgo(2), "Pat Reyes (pat@example.com)"),
    row("b", hoursAgo(20)),
    theirs("t2", hoursAgo(30 * 24), "sam@example.com"),
  ];

  const byRecency = railGroups(rows, "recency", PORTAL_ONLY, NOW);
  expect(byRecency.map((group) => group.label)).toEqual(["Today", "Yesterday", "Other members"]);
  expect(byRecency[2].rows.map((entry) => entry.conversation_id)).toEqual(["t1", "t2"]);

  const byAgent = railGroups(rows, "agent", PORTAL_ONLY, NOW);
  expect(byAgent.map((group) => group.label)).toEqual(["Assistant", "Other members"]);
  expect(byAgent[0].rows.map((entry) => entry.conversation_id)).toEqual(["a", "b"]);
  expect(byAgent[1].rows.map((entry) => entry.conversation_id)).toEqual(["t1", "t2"]);

  expect(railGroups([row("a", hoursAgo(1))], "recency", PORTAL_ONLY, NOW).map((group) => group.label)).toEqual([
    "Today",
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

  expect(railGroups(rows, "recency", PORTAL_ONLY, NOW)).toEqual([
    { label: "Today", rows: [rows[0]] },
  ]);

  const withSlack = railGroups(rows, "recency", { terminal: false, slack: true }, NOW);
  expect(withSlack.map((group) => group.label)).toEqual(["Today", "Other members"]);
  expect(withSlack[0].rows.map((entry) => entry.conversation_id)).toEqual(["a", "s1"]);
  expect(withSlack[1].rows.map((entry) => entry.conversation_id)).toEqual(["s2"]);

  const withTerminal = railGroups(rows, "agent", { terminal: true, slack: false }, NOW);
  expect(withTerminal.map((group) => group.rows.map((entry) => entry.conversation_id))).toEqual([
    ["a", "u1"],
  ]);

  const withBoth = railGroups(rows, "recency", EVERY_SURFACE, NOW);
  expect(withBoth[0].rows.map((entry) => entry.conversation_id)).toEqual(["a", "s1", "u1"]);
});

test("a browser holding no filter admits neither surface, and holds what a member names", () => {
  expect(heldRailShown()).toEqual(PORTAL_ONLY);

  holdRailShown({ terminal: true, slack: false });
  expect(heldRailShown()).toEqual({ terminal: true, slack: false });

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

/** The menu holds the rest of the document `aria-hidden` while it is open, so the rail is read only
 *  once it is shut — as a member reads it. */
async function shutMenu() {
  await userEvent.keyboard("{Escape}");
  await userEvent.keyboard("{Escape}");
}

async function filterBy(label: string) {
  await userEvent.click(await screen.findByRole("button", { name: "Conversation settings" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "Filter" }));
  await userEvent.click(await screen.findByRole("menuitemcheckbox", { name: label }));
  await shutMenu();
}

test("the filter admits a surface into the rail and the browser keeps the choice", async () => {
  wire({ "/api/chats": () => json({ chats: [CHAT_ROW, SLACK_CHAT, TERMINAL_CHAT] }) });
  const first = render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByRole("button", { name: /Pick one thread/ })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Deploy question/ })).toBeNull();
  expect(screen.queryByRole("button", { name: /Migration run/ })).toBeNull();

  await filterBy("Slack");
  expect(await screen.findByRole("button", { name: /Deploy question/ })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Migration run/ })).toBeNull();

  await filterBy("Terminal");
  expect(await screen.findByRole("button", { name: /Migration run/ })).toBeTruthy();

  first.unmount();
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
  expect(await screen.findByRole("button", { name: /Deploy question/ })).toBeTruthy();
  expect(await screen.findByRole("button", { name: /Migration run/ })).toBeTruthy();
});

/** The narrow layout drops every heading and scrolls the rows sideways. The filter still applies
 *  there, so a control the layout hides is a rail with rows missing and no way to ask for them
 *  back. */
test("the settings control survives the narrow layout that hides the rail's headings", async () => {
  wire({ "/api/chats": () => json({ chats: [CHAT_ROW] }) });
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  const nav = screen.getByRole("navigation", { name: "Conversations" });
  let held = await screen.findByRole("button", { name: "Conversation settings" });
  while (held !== nav) {
    expect(held.className).not.toContain("max-narrow:hidden");
    held = held.parentElement!;
  }
});

test("a tick leaves the filter open, so both surfaces are named in one visit", async () => {
  wire({ "/api/chats": () => json({ chats: [CHAT_ROW, SLACK_CHAT, TERMINAL_CHAT] }) });
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Conversation settings" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "Filter" }));
  await userEvent.click(await screen.findByRole("menuitemcheckbox", { name: "Slack" }));
  await userEvent.click(screen.getByRole("menuitemcheckbox", { name: "Terminal" }));
  expect(
    screen.getAllByRole("menuitemcheckbox").map((item) => item.getAttribute("aria-checked")),
  ).toEqual(["true", "true"]);

  await shutMenu();
  expect(await screen.findByRole("button", { name: /Deploy question/ })).toBeTruthy();
  expect(await screen.findByRole("button", { name: /Migration run/ })).toBeTruthy();
});

test("bumping a conversation moves it to the top", () => {
  const rows = [row("a", hoursAgo(3)), row("b", hoursAgo(4))];
  const bumped = bumpChat(rows, "b", NOW);
  expect(bumped.map((entry) => entry.conversation_id)).toEqual(["b", "a"]);
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
    "/api/chats": () =>
      new Promise<Response>(() => {
        return;
      }),
  });
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findAllByText("Loading…")).toHaveLength(2);
  expect(screen.queryByText(NOT_SHARED)).toBeNull();
});

test("a rail read that fails states so and keeps the rows it has", async () => {
  wire({ "/api/chats": () => new Response("nope", { status: 503 }) });
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  const toast = await screen.findByRole("status");
  expect(toast.textContent).toContain("Conversations did not refresh.");
  expect(toast.textContent).toContain("Error 503 — reload to retry.");
});

test("a rail read the session refuses states nothing, since sign-in answers it", async () => {
  wire({ "/api/chats": () => new Response("unauthorized", { status: 401 }) });
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await waitFor(() => expect(screen.queryByText("Loading…")).toBeNull());
  expect(screen.queryByRole("status")).toBeNull();
});

test("a new-conversation link naming no agent of this workspace says so", async () => {
  location.hash = "#/new/99999999-9999-4999-8999-999999999999";
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByText("No such agent.")).toBeTruthy();
});

test("a first message opens a conversation, lands it in the rail, and routes to it", async () => {
  const posts: string[] = [];
  wire({
    "/chat": (url) => {
      posts.push(url);
      return json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello there" });
    },
  });
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await userEvent.type(screen.getByLabelText("Message the agent"), "hello there");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  await waitFor(() => expect(location.hash).toBe("#/c/" + CONVO_ID));
  expect(posts[0]).toContain("?conversation=new");
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
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await userEvent.type(screen.getByLabelText("Message the agent"), "first half");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  // Until that POST answers, nothing here knows which conversation it opened, and a second send to
  // the `new` sentinel opens another one: the two halves would end up in separate conversations,
  // each answered without the other. So the composer holds the words rather than founding again.
  await userEvent.type(screen.getByLabelText("Message the agent"), "second half");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  expect(posts.length).toBe(1);
  expect((screen.getByLabelText("Message the agent") as HTMLInputElement).value).toBe("second half");

  found({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "first half", opened_run: true });
  await waitFor(() => expect(location.hash).toBe("#/c/" + CONVO_ID));

  // The conversation exists now, so the second message joins it mid-turn instead of founding.
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
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await userEvent.type(screen.getByLabelText("Message the agent"), "one thought");
  const form = screen.getByLabelText("Message the agent").closest("form");

  // Both submits read the composer of one render, so a held form cannot be what keeps the second
  // from founding — the send is, which is where the `new` sentinel is spent.
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
    "/api/chats": () =>
      new Promise<Response>((resolve) => {
        releaseRail = resolve;
      }),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "early words" }),
  });
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await userEvent.type(screen.getByLabelText("Message the agent"), "early words");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(location.hash).toBe("#/c/" + CONVO_ID));
  expect(within(screen.getByTestId("log")).getByText("early words")).toBeTruthy();
  expect(screen.queryByText(NOT_SHARED)).toBeNull();

  releaseRail!(
    json({
      chats: [{ ...CHAT_ROW, conversation_id: "88888888-8888-4888-8888-888888888888" }],
    }),
  );
  expect(await screen.findByRole("button", { name: /Pick one thread/ })).toBeTruthy();
  expect(screen.getByRole("button", { name: /early words/ })).toBeTruthy();
  expect(within(screen.getByTestId("log")).getByText("early words")).toBeTruthy();
});

test("a rail row opens its conversation's transcript", async () => {
  const { calls } = wire({
    "/api/chats": () => json({ chats: [CHAT_ROW] }),
    "/transcript": () => json({ messages: [{ role: "user", text: "earlier words" }] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

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
    "/api/chats": () => json({ chats: [newer, older] }),
    "/transcript": () => json({ messages: [] }),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: older.title }),
  });
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: /Pick one thread/ }));
  await userEvent.type(screen.getByLabelText("Message the agent"), "follow-up");
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
  wire({ "/api/chats": () => json({ chats: [foreign] }) });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  const railRow = await screen.findByRole("button", { name: /An ops question/ });
  expect(railRow.textContent).toBe("An ops question");
  fireEvent.focus(railRow);
  expect((await screen.findByRole("tooltip")).textContent).toBe("Second");
});

test("a row from another surface draws its glyph and states the surface's own name", async () => {
  const slack = {
    ...CHAT_ROW,
    surface: "slack",
    surface_label: "Direct message",
    title: "Slack question",
  };
  holdRailShown(EVERY_SURFACE);
  wire({ "/api/chats": () => json({ chats: [slack] }) });
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  const railRow = await screen.findByRole("button", { name: /Slack question/ });
  expect(railRow.textContent).toBe("Slack question");
  expect(railRow.querySelector(".tabler-icon-brand-slack")).not.toBeNull();
  fireEvent.focus(railRow);
  expect((await screen.findByRole("tooltip")).textContent).toBe("Direct message");
});

test("a cli row draws the terminal glyph and reads as Terminal, never as the surface's own name", async () => {
  const cli = { ...CHAT_ROW, surface: "ufo", surface_label: null, title: "Deploy the branch" };
  holdRailShown(EVERY_SURFACE);
  wire({ "/api/chats": () => json({ chats: [cli] }) });
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  const railRow = await screen.findByRole("button", { name: /Deploy the branch/ });
  expect(railRow.querySelector(".tabler-icon-terminal-2")).not.toBeNull();
  fireEvent.focus(railRow);
  expect((await screen.findByRole("tooltip")).textContent).toBe("Terminal");
});

test("the surface glyph is drawn at the sidebar's glyph size, not at the row's text size", async () => {
  const slack = { ...CHAT_ROW, surface: "slack", surface_label: "#ops", title: "Slack question" };
  holdRailShown(EVERY_SURFACE);
  wire({ "/api/chats": () => json({ chats: [slack] }) });
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  const railRow = await screen.findByRole("button", { name: /Slack question/ });
  const drawn = railRow.querySelector(".tabler-icon-brand-slack")!.getAttribute("class")!.split(" ");
  expect(drawn).toContain("size-(--size-glyph)");
  expect(drawn).not.toContain("size-icon");
});

test("a portal row draws no glyph — the rail is read where those conversations happen", async () => {
  wire({ "/api/chats": () => json({ chats: [CHAT_ROW] }) });
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  const railRow = await screen.findByRole("button", { name: /Pick one thread/ });
  expect(railRow.querySelector(".tabler-icon")).toBeNull();
});

test("a row whose title is the whole of it is no tooltip trigger", async () => {
  wire({ "/api/chats": () => json({ chats: [CHAT_ROW] }) });
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  const railRow = await screen.findByRole("button", { name: /Pick one thread/ });
  expect(railRow.getAttribute("data-state")).toBeNull();
  expect(railRow.getAttribute("aria-describedby")).toBeNull();
});

test("a conversation another member spoke stands at the foot and names them", async () => {
  const colleague = {
    ...CHAT_ROW,
    conversation_id: "55555555-5555-4555-8555-555555555555",
    title: "The deploy thread",
    mine: false,
    speaker: "Pat Reyes (pat@example.com)",
  };
  wire({ "/api/chats": () => json({ chats: [CHAT_ROW, colleague] }) });
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  const railRow = await screen.findByRole("button", { name: /The deploy thread/ });
  expect(railRow.textContent).toBe("The deploy thread");
  fireEvent.focus(railRow);
  expect((await screen.findByRole("tooltip")).textContent).toContain("Pat Reyes");
  const section = railRow.closest("section");
  expect(section?.textContent).toContain("Other members");
  expect(section?.textContent).not.toContain("Pick one thread");
  expect(
    screen.getByRole("button", { name: /Pick one thread/ }).closest("section")?.textContent,
  ).not.toContain("Other members");
});

test("an origin rail row opens the read-only pane, never the live chat", async () => {
  const slack = {
    ...CHAT_ROW,
    surface: "slack",
    surface_label: "Direct message",
    title: "Slack question",
  };
  const linked = {
    id: CONVO_ID,
    surface: "slack",
    surface_label: "Direct message",
    audience: "member:0a1b2c3d-0000-4000-8000-000000000009",
    member_email: MEMBER.email,
    description: "",
    speakers: [],
    turn_count: 1,
    created_at: "2026-07-30T10:00:00",
    last_turn_at: "2026-07-30T11:00:00",
    readable: true,
    disclosable: false,
    agent: { id: AGENT.id, name: AGENT.name },
  };
  holdRailShown(EVERY_SURFACE);
  wire({
    "/api/chats": (url) =>
      url.includes("conversation=")
        ? json({ chats: [slack], conversation: linked })
        : json({ chats: [slack] }),
    "/transcript": () => json({ messages: [{ role: "user", text: "slack words" }] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: /Slack question/ }));

  expect(await screen.findByText("slack words")).toBeTruthy();
  expect(screen.getByText(/read-only here/)).toBeTruthy();
  expect(screen.queryByLabelText("Message the agent")).toBeNull();
});

test("a private extension conversation opens the live chat", async () => {
  const sweep = {
    ...CHAT_ROW,
    agent_id: "22222222-2222-4222-8222-222222222222",
    agent_name: "Daily-Brief",
    agent_model: "claude-sonnet-5",
    surface: "extension:sweep",
    surface_label: null,
    title: "Daily brief",
  };
  wire({
    "/api/chats": () => json({ chats: [sweep] }),
    "/transcript": () => json({ messages: [{ role: "assistant", text: "Work to finish" }] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: /Daily brief/ }));

  expect(await screen.findByText("Work to finish")).toBeTruthy();
  const crumb = within(screen.getByRole("navigation", { name: "Breadcrumb" }));
  expect(crumb.getByText("Daily brief")).toBeTruthy();
  // The agent holds the conversation but is not one this member can open, so it is named and not
  // linked.
  expect(crumb.getByText("Daily-Brief")).toBeTruthy();
  expect(crumb.queryByRole("button", { name: "Back to daily-brief" })).toBeNull();
  expect(screen.queryByText("claude-sonnet-5")).toBeNull();
  expect(screen.getByLabelText("Message the agent")).toBeTruthy();
  expect(screen.queryByText(/read-only here/)).toBeNull();
});

test("a conversation no read of this account's answers is named unshared, not missing", async () => {
  location.hash = "#/c/99999999-9999-4999-8999-999999999999";
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

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
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

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
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

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
    ...fields,
  };
}

test("a Slack conversation permalink opens its read-only transcript", async () => {
  location.hash = "#/c/" + CONVO_ID;
  wire({
    "/api/chats": (url) =>
      url.includes("conversation=")
        ? json({ chats: [], conversation: slackConversation() })
        : json({ chats: [] }),
    ["/conversations/" + CONVO_ID + "/transcript"]: () =>
      json({
        messages: [
          { role: "user", text: "from Slack" },
          { role: "assistant", text: "reply in Slack" },
        ],
      }),
  });
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByText("from Slack")).toBeTruthy();
  expect(screen.getByText("reply in Slack")).toBeTruthy();
  expect(screen.queryByLabelText("Message the agent")).toBeNull();
  expect(
    screen.getByText("This conversation is read-only here. Reply in Slack to continue it."),
  ).toBeTruthy();
  expect(screen.getByText("from Slack").closest("main")).not.toBeNull();
  expect(
    within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByText(
      "Warehouse restock plan",
    ),
  ).toBeTruthy();
});

/** The pane a conversation another surface holds is read in is headed the way a portal chat's is:
 *  the agent holding it and what it is called — never a list of conversations
 *  standing over the transcript in place of a header. */
test("a Slack conversation is headed like a web thread, marked with its way out to Slack", async () => {
  location.hash = "#/c/" + CONVO_ID;
  wire({
    "/api/chats": (url) =>
      url.includes("conversation=")
        ? json({ chats: [], conversation: slackConversation() })
        : json({ chats: [] }),
    ["/conversations/" + CONVO_ID + "/transcript"]: () =>
      json({ messages: [{ role: "user", text: "from Slack" }] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

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

/** A terminal session is not a place a link can land, so the same mark states the surface and goes
 *  nowhere. */
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
    ["/conversations/" + CONVO_ID + "/transcript"]: () =>
      json({ messages: [{ role: "user", text: "from the CLI" }] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await screen.findByText("from the CLI");
  const header = within(screen.getByRole("main"));
  expect(header.getByText("Deploy the branch")).toBeTruthy();
  expect(header.getByText("Terminal")).toBeTruthy();
  expect(header.queryByRole("link")).toBeNull();
  expect(
    screen.getByText("This conversation is read-only here. Reply in Terminal to continue it."),
  ).toBeTruthy();
});

/** A conversation another surface holds shares files the same way the chat does, so its markdown
 *  cards open the artifacts sidebar there too — a read-only transcript is not a pane without a
 *  sidebar. */
test("a markdown file in a Slack conversation opens the artifacts sidebar", async () => {
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
    ["/conversations/" + CONVO_ID + "/transcript"]: () =>
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
            },
          })
        : json({ chats: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  const card = await screen.findByRole("button", { name: "notes.md" });
  expect(screen.queryByRole("link", { name: "notes.md" })).toBeNull();
  await userEvent.click(card);
  expect(location.hash).toBe("#/c/" + CONVO_ID + "?slot=artifacts");
  const pane = await screen.findByRole("complementary", { name: "Artifacts" });
  expect(await within(pane).findByText("notes.md")).toBeTruthy();
  await userEvent.click(within(pane).getByRole("button", { name: "Close slot" }));
  expect(location.hash).toBe("#/c/" + CONVO_ID);
});

test("the new-conversation control targets the main agent, and offers no other", async () => {
  wire({});
  const single = render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
  await userEvent.click(screen.getByRole("button", { name: "New conversation" }));
  expect(location.hash).toBe("#/new/" + AGENT_ID);
  single.unmount();

  location.hash = "";
  wire({});
  render(<App agents={[AGENT, SECOND]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
  await userEvent.click(screen.getByRole("button", { name: "New conversation" }));

  expect(location.hash).toBe("#/new/" + AGENT_ID);
  expect(await screen.findByLabelText("Message the agent")).toBeTruthy();
  // A conversation that does not exist yet is headed by nothing; the composer's own picker names
  // the agent that would hold it.
  expect(screen.queryByRole("navigation", { name: "Breadcrumb" })).toBeNull();
  expect(screen.getByRole("combobox", { name: "Agent" }).textContent).toBe("Assistant");
});

test("the agents index opens the agent's page, and chat starts the conversation", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  wire({ "/settings": () => json(SETTINGS), "/connections": () => json({ connections: [] }) });
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
  await screen.findByRole("main");
  expect(screen.queryByRole("button", { name: "New conversation" })).toBeNull();

  location.hash = "#/agents";
  await openAgentRow("Assistant");
  expect(location.hash).toBe("#/agents/" + AGENT_ID);

  await userEvent.click(screen.getByRole("button", { name: "Chat" }));
  await userEvent.click(screen.getByRole("button", { name: "New conversation" }));
  expect(location.hash).toBe("#/new/" + AGENT_ID);
  expect(await screen.findByLabelText("Message the agent")).toBeTruthy();
  expect(screen.queryByRole("navigation", { name: "Breadcrumb" })).toBeNull();
});

test("the conversations rail stands in chat and in no other category", async () => {
  wire({
    "/api/chats": () => json({ chats: [CHAT_ROW] }),
    "/transcript": () => json({ messages: [] }),
    "/workspace/artifacts": () => json({ artifacts: [] }),
    "/objects/site": () => objectIndex(SITE_KIND, []),
  });
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByRole("navigation", { name: "Conversations" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "New conversation" })).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Artifacts" }));
  await waitFor(() =>
    expect(screen.queryByRole("navigation", { name: "Conversations" })).toBeNull(),
  );
  expect(screen.queryByRole("button", { name: "New conversation" })).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Chat" }));
  expect(await screen.findByRole("navigation", { name: "Conversations" })).toBeTruthy();
  expect(await screen.findByRole("button", { name: /Pick one thread/ })).toBeTruthy();
});

test("the chat header names the agent holding the conversation and what it is called, never the model", async () => {
  wire({
    "/api/chats": () => json({ chats: [CHAT_ROW] }),
    "/transcript": () => json({ messages: [] }),
    "/settings": () => new Response("nope", { status: 503 }),
  });
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: /Pick one thread/ }));
  const crumb = within(screen.getByRole("navigation", { name: "Breadcrumb" }));
  expect(crumb.getByText("Pick one thread")).toBeTruthy();
  expect(crumb.getByText("Assistant")).toBeTruthy();
  expect(crumb.queryByText(AGENT.model)).toBeNull();

  await userEvent.click(crumb.getByRole("button", { name: "Back to Assistant" }));
  expect(location.hash).toBe("#/agents/" + AGENT_ID);
});

test("a deep link is not blamed while the rail is the thing that failed", async () => {
  location.hash = "#/c/" + CONVO_ID;
  wire({ "/api/chats": () => new Response("nope", { status: 500 }) });
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

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
    "/api/chats": () => json({ chats: [newer, older] }),
    "/transcript": () => json({ messages: [] }),
    "/chat": () => new Response("nope", { status: 500 }),
  });
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: /Pick one thread/ }));
  await userEvent.type(screen.getByLabelText("Message the agent"), "doomed");
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
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByText("linked words")).toBeTruthy();
  expect(await screen.findByRole("button", { name: /Pick one thread/ })).toBeTruthy();
});

test("a hash naming an agent this member cannot reach reports it", async () => {
  location.hash = "#/agents/99999999-9999-4999-8999-999999999999/settings";
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByText("No such agent.")).toBeTruthy();
  expect(screen.queryByRole("tab", { name: "Settings" })).toBeNull();
});

test("the top bar marks the category the member is in and leaves the others off", async () => {
  wire({
    "/workspace/team": () => json({ members: [], can_add: false, domain: null }),
    "/workspace/radar": () => json({ runs: [] }),
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, []),
    "/objects/source_trigger": () => objectIndex(TRIGGER_KIND, []),
    "/workspace/memory": () => json({ available: true, kinds: [], matches: [] }),
    "/skills": () => json({ skills: [] }),
    "/objects/site": () => objectIndex(SITE_KIND, []),
    "/workspace/artifacts": () => json({ artifacts: [] }),
    "/settings": () => new Response("nope", { status: 503 }),
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  const marked = () =>
    ["Chat", "Apps", "Artifacts", "Radar", "Workspace"].filter(
      (name) => screen.getByRole("button", { name }).getAttribute("aria-current") === "true",
    );

  expect(marked()).toEqual(["Chat"]);

  await userEvent.click(screen.getByRole("button", { name: "Radar" }));
  await waitFor(() => expect(marked()).toEqual(["Radar"]));

  await userEvent.click(screen.getByRole("button", { name: "Workspace" }));
  expect(marked()).toEqual(["Workspace"]);
  expect(await screen.findByRole("tab", { name: "Team" })).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Team" }).getAttribute("aria-selected")).toBe("true");

  await userEvent.click(screen.getByRole("button", { name: "Artifacts" }));
  await waitFor(() => expect(marked()).toEqual(["Artifacts"]));
  expect(await screen.findByText(NO_ARTIFACTS)).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Apps" }));
  expect(marked()).toEqual(["Apps"]);
  const index = within(await screen.findByRole("navigation", { name: "Agents" }));
  expect(index.getByText("Assistant")).toBeTruthy();
  expect(index.getByText("Second")).toBeTruthy();
});

test("a failed rail read states it and retries on demand", async () => {
  let failures = 0;
  wire({
    "/api/chats": () => {
      failures += 1;
      return failures === 1 ? new Response("nope", { status: 500 }) : json({ chats: [CHAT_ROW] });
    },
  });
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByText("Couldn't load conversations.")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByRole("button", { name: /Pick one thread/ })).toBeTruthy();
});
