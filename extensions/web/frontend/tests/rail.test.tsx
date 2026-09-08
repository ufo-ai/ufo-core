import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { agentName } from "@/lib/agentName";
import { appOrder, bumpChat, mergeChats, stampIso, type ChatRow } from "@/lib/rail";
import { railState } from "@/lib/railStore";
import { newChatHash } from "@/lib/route";
import type { Agent } from "@/lib/types";

import { AGENT, AGENT_ID, atPhoneWidth, CHAT_APP, CHAT_APP_ID, CHAT_ROW, chatsOnWire, conversationObject, CONVO_ID, destination, json, MEMBER, objectIndex, openAgentRow, SECOND, SECOND_ID, SETTINGS, SITE_KIND, TASK_KIND, TRIGGER_KIND, TURN_ID, useStreamFake, wire } from "./harness";

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
    "/api/chats": () => json({ chats: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await waitFor(() => expect(railState().phase).toBe("ready"));
  expect(railState().rows.map((entry) => entry.title)).toEqual([
    CHAT_ROW.title,
    "Second page thread",
  ]);
  expect(calls).toBe(2);
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

test("pinned apps stand in the order the member pinned them, whatever their names", () => {
  const apps = [app("brief", "Brief"), app("radar", "Radar"), app("wiki", "Wiki")];
  expect(ids(appOrder(apps, ["wiki", "brief", "radar"]))).toEqual(["wiki", "brief", "radar"]);
  expect(ids(appOrder(apps, ["brief"]))).toEqual(["brief", "radar", "wiki"]);
});

test("the apps under the pins stand by name, whatever they have been doing", () => {
  const apps = [app("zed", "Zed"), app("radar", "Radar"), app("apollo", "Apollo")];
  expect(ids(appOrder(apps, []))).toEqual(["apollo", "radar", "zed"]);
  expect(ids(appOrder(apps, ["zed"]))).toEqual(["zed", "apollo", "radar"]);
});

test("a pin no live app answers draws nothing", () => {
  const apps = [app("radar", "Radar")];
  expect(ids(appOrder(apps, ["removed", "radar"]))).toEqual(["radar"]);
});

test("a pin moves an app to the top and hides nothing", () => {
  const many = "abcdefghij".split("").map((id) => app(id, id.toUpperCase()));
  expect(ids(appOrder(many, []))).toEqual(ids(many));

  expect(ids(appOrder(many, ["i"]))).toEqual([
    "i",
    "a",
    "b",
    "c",
    "d",
    "e",
    "f",
    "g",
    "h",
    "j",
  ]);
  expect(ids(appOrder(many, []))).toEqual(ids(many));
});

test("every app the workspace has is drawn, however many there are", () => {
  const many = Array.from({ length: 30 }, (_, at) => app("a" + at, "A" + at));
  expect(appOrder(many, []).length).toBe(30);
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
    "/objects/conversation$": () =>
      new Promise<Response>(() => {
        return;
      }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findAllByText("Loading…")).toHaveLength(1);
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

  await userEvent.type(screen.getByLabelText("Ask UFO"), "hello there");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  await waitFor(() => expect(location.hash).toBe("#/c/" + CONVO_ID));
  expect(posts[0]).toContain("?conversation=new");
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

  await userEvent.type(screen.getByLabelText("Ask UFO"), "first half");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  // Until that POST answers nothing knows which conversation it opened, and a second send to the `new`
  // sentinel opens another one, so the composer holds the words rather than founding again.
  await userEvent.type(screen.getByLabelText("Ask UFO"), "second half");
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

  await userEvent.type(screen.getByLabelText("Ask UFO"), "one thought");
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

  await userEvent.type(screen.getByLabelText("Ask UFO"), "early words");
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
  expect(screen.queryByText(/read-only here/)).toBeNull();
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
    "/api/chats": (url) =>
      url.includes("conversation=")
        ? new Response("missing or unknown session cookie", { status: 401 })
        : json({ chats: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

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
    description: "Warehouse restock plan",
    source: "https://acme.slack.com/archives/C1/p1700000000000100",
    turn_count: 1,
    created_at: "2026-08-01T08:00:00Z",
    last_turn_at: "2026-08-01T08:01:00Z",
    readable: true,
    disclosable: false,
    commentable: true,
    ...fields,
  };
}

test("a Slack conversation permalink opens a live comment chat", async () => {
  location.hash = "#/c/" + CONVO_ID;
  wire({
    "/api/chats": (url) =>
      url.includes("conversation=")
        ? json({ chats: [], conversation: slackConversation() })
        : json({ chats: [] }),
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
  expect(screen.queryByText(/read-only here/)).toBeNull();
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
    "/api/chats": (url) =>
      url.includes("conversation=")
        ? json({ chats: [], conversation: slackConversation() })
        : json({ chats: [] }),
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
    "/api/chats": (url) =>
      url.includes("conversation=")
        ? json({ chats: [], conversation: terminal })
        : json({ chats: [] }),
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
  expect(screen.queryByText(/read-only here/)).toBeNull();
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
              commentable: true,
            },
          })
        : json({ chats: [] }),
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
  expect(location.hash).toBe("#/agents/" + AGENT_ID);
});

test("a deep link is not blamed while the rail is the thing that failed", async () => {
  location.hash = "#/c/" + CONVO_ID;
  wire({ "/objects/conversation$": () => new Response("nope", { status: 500 }) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const notes = await screen.findAllByText("Couldn't load conversations.");
  expect(notes.length).toBe(1);
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
    "/api/chats": (url) =>
      url.includes("conversation=") ? json({ chats: [CHAT_ROW] }) : json({ chats: [] }),
    "/transcript": () => json({ messages: [{ role: "user", text: "linked words" }] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("linked words")).toBeTruthy();
  await waitFor(() =>
    expect(railState().rows.map((entry) => entry.conversation_id)).toContain(CONVO_ID),
  );
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
    "/workspace/first-run": () => json({ providers: [], connectors: [] }),
    "/github/coverage": () => json({ api: false, sources: false }),
    "/settings": () => new Response("nope", { status: 503 }),
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  const marked = (rail: ReturnType<typeof within>) =>
    ["New chat", "Connectors", "Workspace"].filter(
      (name) => rail.getByRole("button", { name }).getAttribute("aria-current") === "true",
    );

  const home = await openRail();
  expect(marked(home)).toEqual(["New chat"]);

  await userEvent.click(home.getByRole("button", { name: "Workspace" }));
  await waitFor(() => expect(destination()).toBe("Team"));
  const team = await openRail();
  expect(marked(team)).toEqual(["Workspace"]);

  await userEvent.click(team.getByRole("button", { name: "Connectors" }));
  const connectors = await openRail();
  await waitFor(() => expect(marked(connectors)).toEqual(["Connectors"]));

  const index = within(connectors.getByRole("navigation", { name: "Apps" }));
  expect(index.getByRole("button", { name: "Assistant" })).toBeTruthy();
  expect(index.getByRole("button", { name: "Second" })).toBeTruthy();
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
