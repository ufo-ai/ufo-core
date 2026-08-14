import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import {
  bumpChat,
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
  TURN_ID,
  SITE_KIND,
  TASK_KIND,
  TRIGGER_KIND,
  json,
  objectIndex,
  pressRow,
  useStreamFake,
  wire,
} from "./harness";

beforeEach(() => {
  useStreamFake();
});

const NOW = new Date(2026, 7, 1, 12, 0, 0);

const NOT_SHARED = "This conversation is not shared with this account.";

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
  expect(railGroups([row("a", hoursAgo(4))], "recency", NOW)).toHaveLength(1);
});

test("agent sort groups rows under their agent and keeps recency within each group", () => {
  const grouped = railGroups(
    [
      row("a", hoursAgo(1)),
      { ...row("b", hoursAgo(2)), agent_name: "support" },
      row("c", hoursAgo(3)),
    ],
    "agent",
    NOW,
  );
  expect(grouped.map((group) => group.label)).toEqual(["assistant", "support"]);
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

  const byRecency = railGroups(rows, "recency", NOW);
  expect(byRecency.map((group) => group.label)).toEqual(["Today", "Yesterday", "Other members"]);
  expect(byRecency[2].rows.map((entry) => entry.conversation_id)).toEqual(["t1", "t2"]);

  const byAgent = railGroups(rows, "agent", NOW);
  expect(byAgent.map((group) => group.label)).toEqual(["assistant", "Other members"]);
  expect(byAgent[0].rows.map((entry) => entry.conversation_id)).toEqual(["a", "b"]);
  expect(byAgent[1].rows.map((entry) => entry.conversation_id)).toEqual(["t1", "t2"]);

  expect(railGroups([row("a", hoursAgo(1))], "recency", NOW).map((group) => group.label)).toEqual([
    "Today",
  ]);
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
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findAllByText("Loading…")).toHaveLength(2);
  expect(screen.queryByText(NOT_SHARED)).toBeNull();
});

test("a rail read that fails states so and keeps the rows it has", async () => {
  wire({ "/api/chats": () => new Response("nope", { status: 503 }) });
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  const toast = await screen.findByRole("status");
  expect(toast.textContent).toContain("Conversations did not refresh.");
  expect(toast.textContent).toContain("Error 503 — reload to retry.");
});

test("a rail read the session refuses states nothing, since sign-in answers it", async () => {
  wire({ "/api/chats": () => new Response("unauthorized", { status: 401 }) });
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await waitFor(() => expect(screen.queryByText("Loading…")).toBeNull());
  expect(screen.queryByRole("status")).toBeNull();
});

test("a new-conversation link naming no agent of this workspace says so", async () => {
  location.hash = "#/new/99999999-9999-4999-8999-999999999999";
  wire({});
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

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
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

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
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

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
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

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
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

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
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

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
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

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
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  const railRow = await screen.findByRole("button", { name: /An ops question/ });
  expect(railRow.textContent).toBe("An ops question");
  fireEvent.focus(railRow);
  expect((await screen.findByRole("tooltip")).textContent).toBe("second");
});

test("a row from another surface draws its glyph and states the surface's own name", async () => {
  const slack = {
    ...CHAT_ROW,
    surface: "slack",
    surface_label: "Direct message",
    title: "Slack question",
  };
  wire({ "/api/chats": () => json({ chats: [slack] }) });
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  const railRow = await screen.findByRole("button", { name: /Slack question/ });
  expect(railRow.textContent).toBe("Slack question");
  expect(railRow.querySelector(".tabler-icon-brand-slack")).not.toBeNull();
  fireEvent.focus(railRow);
  expect((await screen.findByRole("tooltip")).textContent).toBe("Direct message");
});

test("a cli row draws the terminal glyph and reads as CLI, never as the surface's own name", async () => {
  const cli = { ...CHAT_ROW, surface: "ufo", surface_label: null, title: "Deploy the branch" };
  wire({ "/api/chats": () => json({ chats: [cli] }) });
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  const railRow = await screen.findByRole("button", { name: /Deploy the branch/ });
  expect(railRow.querySelector(".tabler-icon-terminal-2")).not.toBeNull();
  fireEvent.focus(railRow);
  expect((await screen.findByRole("tooltip")).textContent).toBe("CLI");
});

test("a portal row draws no glyph — the rail is read where those conversations happen", async () => {
  wire({ "/api/chats": () => json({ chats: [CHAT_ROW] }) });
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  const railRow = await screen.findByRole("button", { name: /Pick one thread/ });
  expect(railRow.querySelector(".tabler-icon")).toBeNull();
});

test("a row whose title is the whole of it is no tooltip trigger", async () => {
  wire({ "/api/chats": () => json({ chats: [CHAT_ROW] }) });
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

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
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

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
  wire({
    "/api/chats": (url) =>
      url.includes("conversation=")
        ? json({ chats: [slack], conversation: linked })
        : json({ chats: [slack] }),
    "/transcript": () => json({ messages: [{ role: "user", text: "slack words" }] }),
  });
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: /Slack question/ }));

  expect(await screen.findByText("slack words")).toBeTruthy();
  expect(screen.getByText(/read-only here/)).toBeTruthy();
  expect(screen.queryByLabelText("Message the agent")).toBeNull();
});

test("a conversation no read of this account's answers is named unshared, not missing", async () => {
  location.hash = "#/c/99999999-9999-4999-8999-999999999999";
  wire({});
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

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
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

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
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByText("Error 500 — reload to retry.")).toBeTruthy();
  expect(screen.queryByText(NOT_SHARED)).toBeNull();
});

test("a Slack conversation permalink opens its read-only transcript", async () => {
  location.hash = "#/c/" + CONVO_ID;
  wire({
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
    ["/conversations/" + CONVO_ID + "/transcript"]: () =>
      json({
        messages: [
          { role: "user", text: "from Slack" },
          { role: "assistant", text: "reply in Slack" },
        ],
      }),
  });
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByText("from Slack")).toBeTruthy();
  expect(screen.getByText("reply in Slack")).toBeTruthy();
  expect(screen.queryByLabelText("Message the agent")).toBeNull();
  expect(
    screen.getByText("This conversation is read-only here. Reply in Slack to continue it."),
  ).toBeTruthy();
  expect(screen.getByText("from Slack").closest("main")).not.toBeNull();
  const out = screen.getByRole("link", { name: "#ops-warehouse ↗" });
  expect(out.getAttribute("href")).toBe("https://acme.slack.com/archives/C1/p1700000000000100");
  const heading = screen.getByRole("heading", {
    name: AGENT.name + " · #ops-warehouse ↗ · Workspace",
  });
  expect(heading.contains(out)).toBe(true);
  const drawn = out.className.split(" ");
  expect(drawn).toContain("text-inherit");
  expect(drawn).toContain("no-underline");
  expect(drawn).not.toContain("underline");
  expect(within(out).getByText("↗").className).toContain("text-ink-soft");
});

test("the new-conversation control targets the main agent, or picks among several", async () => {
  wire({});
  const single = render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
  await userEvent.click(screen.getByRole("button", { name: "New conversation" }));
  expect(location.hash).toBe("#/new/" + AGENT_ID);
  single.unmount();

  location.hash = "";
  wire({});
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
  await userEvent.click(screen.getByRole("button", { name: "New conversation" }));
  await userEvent.click(await screen.findByRole("button", { name: "second" }));
  expect(location.hash).toBe("#/new/" + SECOND_ID);
  expect(await screen.findByText("Message second to start.")).toBeTruthy();
});

test("the agents index opens the agent's page, and the sidebar starts the conversation", async () => {
  location.hash = "#/agents/" + AGENT_ID + "/connectors";
  wire({ "/connections": () => json({ connections: [] }) });
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
  await screen.findByRole("main");
  expect(
    within(screen.getByRole("main")).queryByRole("button", { name: "New conversation" }),
  ).toBeNull();

  location.hash = "#/agents";
  await screen.findByText("The agent this workspace answers with by default.");
  await pressRow("assistant");
  expect(location.hash).toBe("#/agents/" + AGENT_ID);

  await userEvent.click(screen.getByRole("button", { name: "New conversation" }));
  expect(location.hash).toBe("#/new/" + AGENT_ID);
  expect(await screen.findByText("Message assistant to start.")).toBeTruthy();
});

test("the chat header names the agent and opens its page", async () => {
  wire({
    "/api/chats": () => json({ chats: [CHAT_ROW] }),
    "/transcript": () => json({ messages: [] }),
    "/overview": () => new Response("nope", { status: 503 }),
  });
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: /Pick one thread/ }));
  await userEvent.click(
    within(screen.getByRole("main")).getByRole("button", { name: "assistant" }),
  );

  expect(location.hash).toBe("#/agents/" + AGENT_ID);
  expect(await screen.findByRole("tab", { name: "Overview" })).toBeTruthy();
});

test("a deep link is not blamed while the rail is the thing that failed", async () => {
  location.hash = "#/c/" + CONVO_ID;
  wire({ "/api/chats": () => new Response("nope", { status: 500 }) });
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

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
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

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
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByText("linked words")).toBeTruthy();
  expect(await screen.findByRole("button", { name: /Pick one thread/ })).toBeTruthy();
});

test("a hash naming an agent this member cannot reach reports it", async () => {
  location.hash = "#/agents/99999999-9999-4999-8999-999999999999/connectors";
  wire({});
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByText("No such agent.")).toBeTruthy();
  expect(screen.queryByRole("tab", { name: "Connectors" })).toBeNull();
});

test("the sidebar marks the section the member is in and leaves the others off", async () => {
  wire({
    "/workspace/team": () => json({ members: [], can_add: false, domain: null }),
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, []),
    "/objects/source_trigger": () => objectIndex(TRIGGER_KIND, []),
    "/workspace/memory": () => json({ available: true, kinds: [], matches: [] }),
    "/skills": () => json({ skills: [] }),
    "/objects/site": () => objectIndex(SITE_KIND, []),
    "/workspace/artifacts": () => json({ artifacts: [] }),
    "/overview": () => new Response("nope", { status: 503 }),
  });
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  const marked = () =>
    ["Agents", "Automations", "Artifacts", "Memory", "Workspace"].filter(
      (name) => screen.getByRole("button", { name }).getAttribute("aria-current") === "true",
    );

  await userEvent.click(screen.getByRole("button", { name: "Automations" }));
  await waitFor(() => expect(marked()).toEqual(["Automations"]));

  await userEvent.click(screen.getByRole("button", { name: "Workspace" }));
  expect(marked()).toEqual(["Workspace"]);
  expect(await screen.findByRole("tab", { name: "Team" })).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Team" }).getAttribute("aria-selected")).toBe("true");

  await userEvent.click(screen.getByRole("button", { name: "Artifacts" }));
  await waitFor(() => expect(marked()).toEqual(["Artifacts"]));
  expect(await screen.findByText(NO_ARTIFACTS)).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Agents" }));
  expect(marked()).toEqual(["Agents"]);
  const index = within(await screen.findByRole("main"));
  expect(index.getByText("assistant")).toBeTruthy();
  expect(index.getByText("second")).toBeTruthy();
  expect(index.getByRole("table").querySelectorAll("tbody tr").length).toBe(2);
});

test("a failed rail read states it and retries on demand", async () => {
  let failures = 0;
  wire({
    "/api/chats": () => {
      failures += 1;
      return failures === 1 ? new Response("nope", { status: 500 }) : json({ chats: [CHAT_ROW] });
    },
  });
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByText("Couldn't load conversations.")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByRole("button", { name: /Pick one thread/ })).toBeTruthy();
});
