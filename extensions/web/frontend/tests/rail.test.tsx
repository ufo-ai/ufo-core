import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { bumpChat, groupChats, mergeChats, relativeTime, type ChatRow } from "@/lib/rail";

import {
  AGENT,
  AGENT_ID,
  CHAT_ROW,
  CONVO_ID,
  MEMBER,
  SECOND,
  SECOND_ID,
  TURN_ID,
  SITE_KIND,
  json,
  objectIndex,
  useStreamFake,
  wire,
} from "./harness";

beforeEach(() => {
  useStreamFake();
});

const NOW = new Date(2026, 7, 1, 12, 0, 0);

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

test("relative time names minutes, hours, days, and dates", () => {
  expect(relativeTime(hoursAgo(1 / 180), NOW)).toBe("now");
  expect(relativeTime(hoursAgo(0.75), NOW)).toBe("45m");
  expect(relativeTime(hoursAgo(9), NOW)).toBe("9h");
  expect(relativeTime(hoursAgo(3 * 24), NOW)).toBe("3d");
  expect(relativeTime(hoursAgo(90 * 24), NOW)).toBe(
    new Date(NOW.getTime() - 90 * 24 * 3_600_000).toLocaleDateString(),
  );
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
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  expect(await screen.findAllByText("Loading…")).toHaveLength(2);
  expect(screen.queryByText("No such conversation.")).toBeNull();
});

test("a new-conversation link naming no agent of this workspace says so", async () => {
  location.hash = "#/new/99999999-9999-4999-8999-999999999999";
  wire({});
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

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
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

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
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  await userEvent.type(screen.getByLabelText("Message the agent"), "early words");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(location.hash).toBe("#/c/" + CONVO_ID));
  expect(within(screen.getByTestId("log")).getByText("early words")).toBeTruthy();
  expect(screen.queryByText("No such conversation.")).toBeNull();

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
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

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
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

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
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} />);

  const railRow = await screen.findByRole("button", { name: /An ops question/ });
  expect(railRow.textContent).toContain("second · ");
});

test("a conversation the rail does not hold says so", async () => {
  location.hash = "#/c/99999999-9999-4999-8999-999999999999";
  wire({});
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  expect(await screen.findByText("No such conversation.")).toBeTruthy();
});

test("the new-conversation control targets the main agent, or picks among several", async () => {
  wire({});
  const single = render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);
  await userEvent.click(screen.getByRole("button", { name: "New conversation" }));
  expect(location.hash).toBe("#/new/" + AGENT_ID);
  single.unmount();

  location.hash = "";
  wire({});
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} />);
  await userEvent.click(screen.getByRole("button", { name: "New conversation" }));
  await userEvent.click(await screen.findByRole("button", { name: "second" }));
  expect(location.hash).toBe("#/new/" + SECOND_ID);
  expect(await screen.findByText("Message second to start.")).toBeTruthy();
});

test("the agent page and the agents index both offer a new conversation", async () => {
  location.hash = "#/agents/" + AGENT_ID + "/skills";
  wire({ "/skills": () => json({ skills: [] }) });
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);
  await userEvent.click(
    within(await screen.findByRole("main")).getByRole("button", { name: "New conversation" }),
  );
  expect(location.hash).toBe("#/new/" + AGENT_ID);
  expect(await screen.findByText("Message assistant to start.")).toBeTruthy();

  location.hash = "#/agents";
  await screen.findByRole("button", { name: "assistant · main agent" });
  await userEvent.click(
    within(screen.getByRole("main")).getByRole("button", { name: "New conversation" }),
  );
  expect(location.hash).toBe("#/new/" + AGENT_ID);
});

test("the chat header names the agent and opens its page", async () => {
  wire({
    "/api/chats": () => json({ chats: [CHAT_ROW] }),
    "/transcript": () => json({ messages: [] }),
    "/overview": () => new Response("nope", { status: 503 }),
  });
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

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
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  const notes = await screen.findAllByText("Couldn't load conversations.");
  expect(notes.length).toBe(2);
  expect(screen.queryByText("No such conversation.")).toBeNull();
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
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

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
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  expect(await screen.findByText("linked words")).toBeTruthy();
  expect(await screen.findByRole("button", { name: /Pick one thread/ })).toBeTruthy();
});

test("a hash naming an agent this member cannot reach reports it", async () => {
  location.hash = "#/agents/99999999-9999-4999-8999-999999999999/skills";
  wire({});
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  expect(await screen.findByText("No such agent.")).toBeTruthy();
  expect(screen.queryByRole("tab", { name: "Skills" })).toBeNull();
});

test("the sidebar marks the section the member is in and leaves the others off", async () => {
  wire({
    "/workspace/team": () => json({ members: [], can_add: false, domain: null }),
    "/objects/site": () => objectIndex(SITE_KIND, []),
    "/overview": () => new Response("nope", { status: 503 }),
  });
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} />);

  await userEvent.click(screen.getByRole("button", { name: "Workspace" }));
  expect(screen.getByRole("button", { name: "Workspace" }).getAttribute("aria-current")).toBe(
    "true",
  );
  expect(screen.getByRole("button", { name: "Agents" }).getAttribute("aria-current")).toBe(
    "false",
  );
  expect(await screen.findByRole("tab", { name: "Team" })).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Team" }).getAttribute("aria-selected")).toBe("true");
  expect(screen.getByRole("tab", { name: "Sites" }).getAttribute("aria-selected")).toBe("false");

  await userEvent.click(screen.getByRole("tab", { name: "Sites" }));
  await waitFor(() =>
    expect(screen.getByRole("tab", { name: "Sites" }).getAttribute("aria-selected")).toBe("true"),
  );
  expect(screen.getByRole("tab", { name: "Team" }).getAttribute("aria-selected")).toBe("false");

  await userEvent.click(screen.getByRole("button", { name: "Agents" }));
  expect(screen.getByRole("button", { name: "Agents" }).getAttribute("aria-current")).toBe(
    "true",
  );
  expect(screen.getByRole("button", { name: "Workspace" }).getAttribute("aria-current")).toBe(
    "false",
  );
  const index = within(await screen.findByRole("main"));
  expect(index.getAllByText("opus").length).toBe(2);
  expect(index.getByRole("button", { name: "assistant · main agent" })).toBeTruthy();
  expect(index.getByRole("button", { name: "second" })).toBeTruthy();
});

test("a failed rail read states it and retries on demand", async () => {
  let failures = 0;
  wire({
    "/api/chats": () => {
      failures += 1;
      return failures === 1 ? new Response("nope", { status: 500 }) : json({ chats: [CHAT_ROW] });
    },
  });
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  expect(await screen.findByText("Couldn't load conversations.")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByRole("button", { name: /Pick one thread/ })).toBeTruthy();
});
