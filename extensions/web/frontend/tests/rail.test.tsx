import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { bumpChat, groupChats, mergeChats, type ChatRow } from "@/lib/rail";

import {
  AGENT,
  AGENT_ID,
  CHAT_ROW,
  CONVO_ID,
  MEMBER,
  NO_SITES,
  SECOND,
  SECOND_ID,
  TURN_ID,
  SITE_KIND,
  TASK_KIND,
  json,
  objectIndex,
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
  };
}

test("chats group by recency in rail order and empty groups are absent", () => {
  const grouped = groupChats(
    [
      row("a", hoursAgo(3)),
      row("b", hoursAgo(20)),
      row("c", hoursAgo(4 * 24)),
      row("d", hoursAgo(20 * 24)),
      row("e", hoursAgo(90 * 24)),
    ],
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
  expect(groupChats([row("a", hoursAgo(4))], NOW)).toHaveLength(1);
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
  expect(railRow.textContent).toContain("second");
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
              member_email: null,
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
    screen.getByText("This conversation is read-only here. Reply in slack to continue it."),
  ).toBeTruthy();
  expect(screen.getByText("from Slack").closest("main")).not.toBeNull();
  expect(
    screen.getByRole("heading", { name: AGENT.name + " · Shared · " + CONVO_ID.slice(0, 8) }),
  ).toBeTruthy();
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

test("the agents index opens a conversation; the agent's own page states settings only", async () => {
  location.hash = "#/agents/" + AGENT_ID + "/connectors";
  wire({ "/connections": () => json({ connections: [] }) });
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
  await screen.findByRole("main");
  expect(
    within(screen.getByRole("main")).queryByRole("button", { name: "New conversation" }),
  ).toBeNull();

  location.hash = "#/agents";
  await screen.findByText("The agent this workspace answers with by default.");
  await userEvent.click(
    within(screen.getByRole("main")).getByRole("button", { name: "New conversation" }),
  );
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
    "/workspace/memory": () => json({ available: true, kinds: [], matches: [] }),
    "/skills": () => json({ skills: [] }),
    "/objects/site": () => objectIndex(SITE_KIND, []),
    "/overview": () => new Response("nope", { status: 503 }),
  });
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  const marked = () =>
    ["Agents", "Scheduled", "Artifacts", "Sites", "Customize", "Workspace"].filter(
      (name) => screen.getByRole("button", { name }).getAttribute("aria-current") === "true",
    );

  await userEvent.click(screen.getByRole("button", { name: "Scheduled" }));
  await waitFor(() => expect(marked()).toEqual(["Scheduled"]));

  await userEvent.click(screen.getByRole("button", { name: "Workspace" }));
  expect(marked()).toEqual(["Workspace"]);
  expect(await screen.findByRole("tab", { name: "Team" })).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Team" }).getAttribute("aria-selected")).toBe("true");

  await userEvent.click(screen.getByRole("button", { name: "Sites" }));
  await waitFor(() => expect(marked()).toEqual(["Sites"]));
  expect(await screen.findByText(NO_SITES)).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Agents" }));
  expect(marked()).toEqual(["Agents"]);
  const index = within(await screen.findByRole("main"));
  expect(index.getAllByText("opus").length).toBe(2);
  expect(index.getByText("assistant")).toBeTruthy();
  expect(index.getByText("second")).toBeTruthy();
  expect(index.getAllByRole("button", { name: "View" }).length).toBe(2);
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
