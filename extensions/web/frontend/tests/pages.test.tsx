import { join } from "node:path";

import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import type { AppInit } from "@/apps/runtime";
import { BASE, REFUSAL_HEADER, SESSION_FAULT_HEADER } from "@/lib/api";
import { agentName } from "@/lib/agentName";
import { agentHash } from "@/lib/route";

import {
  AGENT,
  ARRIVAL_ID,
  CHAT_ROW,
  CONVO_ID,
  MEMBER,
  SECOND_ID,
  TURN_ID,
  json,
  wire,
  type Route,
} from "./harness";

/** jsdom delivers `postMessage` and drives `requestAnimationFrame` on the window's own timers, so both
 *  ride timers captured before the lifecycle loads — clearing the page's would clear the environment's. */
const FRAME_MS = 16;
const environmentTimeout = window.setTimeout.bind(window);
const environmentClear = window.clearTimeout.bind(window);
window.requestAnimationFrame = (callback) =>
  environmentTimeout(() => callback(performance.now()), FRAME_MS);
window.cancelAnimationFrame = (handle) => environmentClear(handle);
window.postMessage = ((message: unknown, targetOrigin: string) => {
  if (targetOrigin === "/") return;
  if (targetOrigin !== "*" && new URL(targetOrigin).origin !== location.origin) return;
  environmentTimeout(() => {
    window.dispatchEvent(new MessageEvent("message", { data: message }));
  }, 0);
}) as typeof window.postMessage;

const applicationLifecycle = await import("@/apps/lifecycle");
vi.doMock("@/apps/lifecycle", () => applicationLifecycle);
const lifecycle = (
  window as unknown as {
    __ufoApplicationLifecycle: { snapshot(): { blockingWork: number } };
  }
).__ufoApplicationLifecycle;

/** jsdom's `window.top` is the window itself, so the runtime's posts land on our own listener. */

const EXTENSIONS = join(import.meta.dirname, "..", "..", "..");

function pagePath(app: string): string {
  return join(EXTENSIONS, "app_" + app, "ufo_ext_app_" + app, "skills", "app-" + app + "-home", "app.tsx");
}

const INIT: AppInit = {
  member: { email: MEMBER.email, admin: true },
  agents: [AGENT],
  agentId: AGENT.id,
  banded: false,
  place: {},
  portal: location.origin,
};

function shell(
  handler: (url: string, init?: RequestInit) => Response | Promise<Response>,
  init: Partial<AppInit> = {},
): () => void {
  const listener = (event: MessageEvent) => {
    const message = event.data as { ufo?: string } | null;
    if (!message || typeof message.ufo !== "string") return;
    if (message.ufo === "ready") {
      window.postMessage({ ufo: "init", ...INIT, ...init }, "*");
      return;
    }
    if (message.ufo !== "call") return;
    const call = event.data as {
      id: string;
      method: string;
      path: string;
      body?: string;
      headers?: Record<string, string>;
    };
    void (async () => {
      try {
        const res = await handler(BASE + call.path, {
          method: call.method,
          headers: call.headers,
          body: call.body,
        });
        window.postMessage(
          {
            ufo: "data",
            id: call.id,
            ok: res.ok,
            status: res.status,
            body: await res.text(),
            refusal: res.headers.get(REFUSAL_HEADER),
            fault: res.headers.get(SESSION_FAULT_HEADER),
          },
          "*",
        );
      } catch (error) {
        window.postMessage(
          { ufo: "data", id: call.id, ok: false, status: 599, body: String(error), refusal: String(error), fault: null },
          "*",
        );
      }
    })();
  };
  window.addEventListener("message", listener);
  return () => window.removeEventListener("message", listener);
}

async function runPage(
  app: string,
  routes: Record<string, Route>,
  init: Partial<AppInit> = {},
): Promise<{ calls: string[] }> {
  vi.resetModules();
  const { calls, handler } = wire(routes);
  shells.push(shell(handler, init));
  const root = document.getElementById("root")!;
  await import(/* @vite-ignore */ pagePath(app));
  const { unmountApp } = await import("@/apps/shell");
  unmounts.push(() => unmountApp(root));
  return { calls };
}

const shells: (() => void)[] = [];
const unmounts: (() => void)[] = [];

/** A `call` the page posted rides `postMessage`, which delivers on a later task — so a shell detached in
 *  the same tick as the unmount leaves any call still queued unanswered, and its lease never settles. */
async function settle(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 50));
    for (const unmount of unmounts.splice(0)) unmount();
  });
  await vi.waitFor(() => expect(lifecycle.snapshot().blockingWork).toBe(0));
  for (const detach of shells.splice(0)) detach();
}

const nativeFetch = window.fetch;
const nativeEventSource = window.EventSource;

beforeEach(() => {
  vi.useRealTimers();
  localStorage.clear();
  document.body.innerHTML = '<div id="root"></div>';
});

afterEach(async () => {
  await settle();
  window.fetch = nativeFetch;
  (window as { EventSource: typeof EventSource }).EventSource = nativeEventSource;
});

const COLLEAGUE = "colleague@example.com";

test("the wiki crumb comes back to the workspace article in the lane rather than the full screen", async () => {
  const moved: string[] = [];
  const watch = (event: MessageEvent) => {
    const message = event.data as { ufo?: string; to?: string } | null;
    if (message?.ufo === "navigate" && typeof message.to === "string") moved.push(message.to);
  };
  window.addEventListener("message", watch);
  try {
    await runPage(
      "wiki",
      {
        "/workspace/memory": () => json({ available: true, matches: [] }),
        "/objects/memory": () => json({ objects: [] }),
        "/objects/member": () =>
          json({ objects: [{ name: COLLEAGUE, email: COLLEAGUE, admin: false, seated: true }] }),
      },
      {
        banded: true,
        place: { opens: ["member/" + COLLEAGUE] },
        crumb: { label: "Wiki", at: new URL(agentHash(AGENT.id), location.origin + BASE).href },
      },
    );

    expect(await screen.findByRole("heading", { level: 1, name: COLLEAGUE })).toBeTruthy();
    await userEvent.click(screen.getByRole("link", { name: "Back to Wiki" }));

    expect(await screen.findByRole("heading", { name: "People" })).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 1, name: COLLEAGUE })).toBeNull();
    expect(moved).toEqual([]);
  } finally {
    window.removeEventListener("message", watch);
  }
});

test("the wiki crumb states the app's own address on the page's own screen", async () => {
  const moved: string[] = [];
  const watch = (event: MessageEvent) => {
    const message = event.data as { ufo?: string; to?: string } | null;
    if (message?.ufo === "navigate" && typeof message.to === "string") moved.push(message.to);
  };
  window.addEventListener("message", watch);
  try {
    await runPage(
      "wiki",
      {
        "/workspace/memory": () => json({ available: true, matches: [] }),
        "/objects/memory": () => json({ objects: [] }),
        "/objects/member": () =>
          json({ objects: [{ name: COLLEAGUE, email: COLLEAGUE, admin: false, seated: true }] }),
      },
      {
        place: { opens: ["member/" + COLLEAGUE] },
        crumb: { label: "Wiki", at: new URL(agentHash(AGENT.id), location.origin + BASE).href },
      },
    );

    expect(await screen.findByRole("heading", { level: 1, name: COLLEAGUE })).toBeTruthy();
    await userEvent.click(screen.getByRole("link", { name: "Back to Wiki" }));

    expect(await screen.findByRole("heading", { name: "People" })).toBeTruthy();
    expect(moved).toEqual([agentHash(AGENT.id)]);
  } finally {
    window.removeEventListener("message", watch);
  }
});

function lapse(after: number): Promise<string> {
  return new Promise((resolve) =>
    applicationLifecycle.setNativeTimeout(() => resolve("nothing"), after),
  );
}

const WIKI_READS = {
  "/objects/memory": () => json({ objects: [] }),
  "/objects/member": () => json({ objects: [] }),
};

test("a reply posted to a page as it unmounts still lands, so its work drains", async () => {
  await runPage("wiki", WIKI_READS);
  await screen.findByRole("heading", { name: "Wiki" });
  const inFlight = window.fetch(BASE + "/objects/memory").then((res) => (res.ok ? "reply" : "refused"));

  await act(async () => {
    for (const unmount of unmounts.splice(0)) unmount();
  });

  expect(await Promise.race([inFlight, lapse(500)])).toBe("reply");
});

test("a page's unmount leaves the animation frames running", async () => {
  /** jsdom drives frames on the window's own interval, so a frame requested before the unmount that
   *  clears the page's timers would otherwise never fire, nor any release after it. */
  await runPage("wiki", WIKI_READS);
  await screen.findByRole("heading", { name: "Wiki" });
  const requested = new Promise<string>((resolve) =>
    window.requestAnimationFrame(() => resolve("frame")),
  );

  await act(async () => {
    for (const unmount of unmounts.splice(0)) unmount();
  });

  expect(await Promise.race([requested, lapse(500)])).toBe("frame");
});

test("page module resets reuse the installed lifecycle", async () => {
  await runPage("wiki", WIKI_READS);
  expect(await screen.findByRole("heading", { name: "Wiki" })).toBeTruthy();

  vi.resetModules();
  await expect(import("@/apps/runtime")).resolves.toBeDefined();
});

test("the wiki page mounts and draws the workspace article", async () => {
  const { calls } = await runPage("wiki", {
    "/objects/memory": () => json({ objects: [] }),
    "/objects/member": () => json({ objects: [] }),
  });
  expect(await screen.findByRole("heading", { name: "Wiki" })).toBeTruthy();
  expect(await screen.findByRole("heading", { name: "People" })).toBeTruthy();
  expect(await screen.findByText("Everyone in this workspace.")).toBeTruthy();
  const rosterCalls = calls.filter((url) => url.includes("/objects/member"));
  expect(rosterCalls.length).toBeGreaterThan(0);
  expect(rosterCalls.every((url) => url.endsWith("/objects/member?agent=" + AGENT.id))).toBe(true);
});

test("the wiki page heads one member under the crumb the shell handed it", async () => {
  const email = "colleague@example.com";
  const { calls } = await runPage(
    "wiki",
    {
      "/workspace/memory": () => json({ available: true, matches: [] }),
      "/objects/memory": () => json({ objects: [] }),
      "/objects/member": () =>
        json({ objects: [{ name: email, email, admin: false, seated: true }] }),
    },
    {
      place: { opens: ["member/" + email] },
      crumb: {
        label: "Wiki",
        at: new URL(agentHash(AGENT.id), location.origin + BASE).href,
      },
    },
  );

  expect(await screen.findByRole("heading", { level: 1, name: email })).toBeTruthy();
  const path = screen.getByRole("navigation", { name: "Breadcrumb" });
  expect(within(path).getByRole("link", { name: "Back to Wiki" }).getAttribute("href")).toBe(
    new URL(agentHash(AGENT.id), location.origin + BASE).href,
  );
  expect(screen.getByRole("button", { name: "Close " + email })).toBeTruthy();
  const rosterCalls = calls.filter((url) => url.includes("/objects/member"));
  expect(rosterCalls.length).toBeGreaterThan(0);
  expect(rosterCalls.every((url) => url.endsWith("/objects/member?agent=" + AGENT.id))).toBe(true);
});

const CONSOLIDATED = "Acme Corp — Moved the billing cutover to 11 March, with Rob Ryan owning it.";
const BREADCRUMB = "Acme Corp — Opened the billing dashboard.";

test("a member's own page draws the summary its consolidated rows were collapsed into", async () => {
  const row = (item_class: string, summary: string) => ({
    name: item_class + "-row",
    summary,
    text: summary,
    subject: "member:" + MEMBER.email,
    item_class,
    memory_kind: "fact",
    written: "2026-08-20T09:00:00+00:00",
    agent_id: AGENT.id,
    created_from_page_id: null,
    created_from_page_title: null,
    created_from_page_stream: null,
  });
  await runPage(
    "wiki",
    {
      "/objects/memory": (url) =>
        json({
          objects: !url.includes("memory_kind=fact")
            ? []
            : url.includes("item_class=semantic")
              ? [row("semantic", CONSOLIDATED)]
              : url.includes("item_class=episodic")
                ? [row("episodic", BREADCRUMB)]
                : [],
        }),
      "/objects/member": () =>
        json({
          objects: [
            { name: MEMBER.email, email: MEMBER.email, admin: false, seated: true },
          ],
        }),
    },
    { place: { opens: ["member/" + MEMBER.email] } },
  );

  expect(await screen.findByRole("heading", { name: "Facts" })).toBeTruthy();
  expect(await screen.findByRole("button", { name: CONSOLIDATED })).toBeTruthy();
  expect(screen.queryByText(BREADCRUMB)).toBeNull();
});

test("the meetings page mounts and draws its bands over its own placeholder", async () => {
  await runPage("meetings", { "/objects/conversation": () => json({ objects: [] }) });
  expect(await screen.findByRole("heading", { name: "Meetings" })).toBeTruthy();
  expect(await screen.findByRole("heading", { name: "Next meetings" })).toBeTruthy();
  expect(await screen.findByRole("heading", { name: "Follow-ups" })).toBeTruthy();
});

test("the code page mounts and draws its review queue", async () => {
  await runPage("code", { "/objects/conversation": () => json({ objects: [] }) });
  expect(await screen.findByRole("heading", { name: "Code" })).toBeTruthy();
});

test("the issues page mounts and draws both of its bands", async () => {
  await runPage("issues", { "/objects/conversation": () => json({ objects: [] }) });
  expect(await screen.findByRole("heading", { name: "Issues" })).toBeTruthy();
  expect(await screen.findByRole("heading", { name: "Approved to implement" })).toBeTruthy();
  expect(await screen.findByRole("heading", { name: "Triaged" })).toBeTruthy();
});

test("the notification page lists the source, state, repeats, and first activity", async () => {
  const name = "99999999999949998999999999999999";
  const subject = "CI is failing on the release branch";
  const raised = "2026-09-12T16:30:00Z";
  const { calls } = await runPage("notification", {
    ["/objects/notification/" + name]: () =>
      json({
        kind: "notification",
        fields: [
          "occurrences",
          "first_raised_at",
          "last_raised_at",
          "triaged_turn",
          "delivered_surface",
        ],
        spec_schema: null,
        applies: false,
        deletes: true,
        name,
        summary: subject + ": Tests failed in three jobs.",
        spec: { subject, body: "Tests failed in three jobs." },
        status: {
          occurrences: 3,
          first_raised_at: "2026-09-12T15:00:00Z",
          last_raised_at: raised,
          triaged_turn: "77777777-7777-4777-8777-777777777777",
          delivered_surface: "slack",
        },
        links: [],
        created_at: "2026-09-12T15:00:00Z",
        updated_at: raised,
      }),
    "/objects/notification": () =>
      json({
        kind: "notification",
        fields: [
          "subject",
          "occurrences",
          "producer",
          "triaged",
          "delivered_surface",
          "created_at",
        ],
        spec_schema: null,
        applies: false,
        deletes: true,
        objects: [
          {
            name,
            summary: subject + ": Tests failed in three jobs.",
            agent_id: AGENT.id,
            agent_name: "notification",
            subject,
            occurrences: 3,
            producer: "code",
            triaged: true,
            delivered_surface: "slack",
            created_at: "2026-09-12T15:00:00Z",
          },
        ],
        next_cursor: null,
      }),
    "/objects/conversation": () => json({ objects: [] }),
  });

  expect(await screen.findByRole("heading", { name: "Inbox" })).toBeTruthy();
  expect((await screen.findAllByRole("columnheader")).map((head) => head.textContent)).toEqual([
    "Subject",
    "Agent",
    "Status",
    "First raised",
  ]);
  const row = (await screen.findByText(subject)).closest("tr")!;
  expect(within(row).getByText("3 times")).toBeTruthy();
  expect(within(row).getByText("Code")).toBeTruthy();
  expect(within(row).getByText("Delivered to Slack")).toBeTruthy();
  expect(
    calls.some((url) => url.includes("order_by=created_at&order=desc")),
  ).toBe(true);

  await userEvent.click(row);
  const detail = await screen.findByRole("dialog");
  expect(within(detail).getAllByText(subject)).toBeTruthy();
  expect(within(detail).getByText("Tests failed in three jobs.")).toBeTruthy();
});

test("the metrics page mounts and states which measure sets are not on", async () => {
  await runPage("metrics", { "/objects/conversation": () => json({ objects: [] }) });
  expect(await screen.findByRole("heading", { name: "Metrics" })).toBeTruthy();
  expect((await screen.findAllByRole("button", { name: "Turn on" })).length).toBeGreaterThan(0);
});

test("the chat page mounts and draws the empty conversation list", async () => {
  await runPage("chat", {
    "/objects/conversation": () => json({ objects: [] }),
  });
  expect(await screen.findByRole("heading", { name: "Chat" })).toBeTruthy();
  expect(await screen.findByText("No conversations yet.")).toBeTruthy();
});

test("the chat page opens a conversation without re-listing, and switching opens does not re-list", async () => {
  const A = CONVO_ID;
  const B = ARRIVAL_ID;
  const conversationRow = (id: string, title: string) => chatListRow(id, title);
  const resolvedChat = (id: string, title: string) => ({
    conversation_id: id,
    agent_id: AGENT.id,
    agent_name: AGENT.name,
    title,
    surface: "web",
    last_at: "2026-08-01T09:00:00.000Z",
  });
  const { calls } = await runPage("chat", {
    "/objects/conversation": () =>
      json({ objects: [conversationRow(A, "Alpha"), conversationRow(B, "Bravo")], next_cursor: null }),
    "/api/chats": (url) =>
      json({ chats: [url.includes(B) ? resolvedChat(B, "Bravo") : resolvedChat(A, "Alpha")] }),
    "/transcript": () => json({ messages: [] }),
  });
  const listReads = () => calls.filter((url) => url.includes("/objects/conversation")).length;

  // Wait for the bridge handshake and the place subscription before driving one, or the post races the
  // mount and is lost.
  await screen.findByRole("heading", { name: "Chat" });

  window.postMessage({ ufo: "place", place: { opens: [A] } }, "*");
  await vi.waitFor(() =>
    expect(calls.some((url) => url.includes("transcript") && url.includes(A))).toBe(true),
  );
  const listedForA = listReads();

  window.postMessage({ ufo: "place", place: { opens: [B] } }, "*");
  await vi.waitFor(() =>
    expect(calls.some((url) => url.includes("transcript") && url.includes(B))).toBe(true),
  );

  expect(listReads()).toBe(listedForA);
});

test("the chat page heads one conversation under the crumb the shell handed it", async () => {
  await runPage(
    "chat",
    {
      "/api/chats": () =>
        json({ chats: [{ ...CHAT_ROW, agent_id: SECOND_ID, agent_name: "second" }] }),
      "/transcript": () => json({ messages: [] }),
      "/slots": () => json({ slots: [] }),
    },
    {
      place: { opens: [CONVO_ID] },
      crumb: {
        label: "Chat",
        at: new URL(agentHash(AGENT.id), location.origin + BASE).href,
      },
    },
  );

  const path = await screen.findByRole("navigation", { name: "Breadcrumb" });
  const crumb = within(path).getByRole("link", { name: "Back to Chat" });
  expect(crumb.getAttribute("href")).toBe(
    new URL(agentHash(AGENT.id), location.origin + BASE).href,
  );
  const modified = new MouseEvent("click", { bubbles: true, cancelable: true, metaKey: true });
  expect(crumb.dispatchEvent(modified)).toBe(true);
  const middle = new MouseEvent("click", { bubbles: true, button: 1, cancelable: true });
  expect(crumb.dispatchEvent(middle)).toBe(true);
  const plain = new MouseEvent("click", { bubbles: true, cancelable: true });
  expect(crumb.dispatchEvent(plain)).toBe(false);
  expect(within(path).getByText(CHAT_ROW.title)).toBeTruthy();
});

test("the chat page opens a Slack or terminal conversation for comments", async () => {
  const SLACK = TURN_ID;
  const slackConversation = {
    id: SLACK,
    agent: { id: AGENT.id, name: AGENT.name },
    surface: "slack",
    surface_label: "#general",
    audience: "member:m1",
    member_email: MEMBER.email,
    description: "Slack thread",
    source: "https://slack.example/archives/x/p1",
    speakers: [MEMBER.email],
    turn_count: 2,
    created_at: "2026-08-01T09:00:00",
    last_turn_at: "2026-08-01T09:00:01",
    readable: true,
    disclosable: false,
    commentable: true,
  };
  const { calls } = await runPage("chat", {
    "/objects/conversation": () => json({ objects: [], next_cursor: null }),
    "/api/chats": () => json({ chats: [], conversation: slackConversation }),
    "/transcript": () => json({ messages: [] }),
  });

  await screen.findByRole("heading", { name: "Chat" });
  window.postMessage({ ufo: "place", place: { opens: [SLACK] } }, "*");

  expect(await screen.findByLabelText("Ask UFO")).toBeTruthy();
  expect(screen.queryByText(/read-only here/)).toBeNull();
  expect(screen.queryByText("This conversation is not available here.")).toBeNull();
  await vi.waitFor(() =>
    expect(calls.some((url) => url.includes("transcript") && url.includes(SLACK))).toBe(true),
  );
});

function chatListRow(id: string, title: string, extra: Record<string, unknown> = {}) {
  return {
    name: id,
    agent_id: AGENT.id,
    agent_name: AGENT.name,
    title,
    surface: "web",
    surface_label: null,
    mine: true,
    speaker: null,
    last_at: new Date().toISOString(),
    ...extra,
  };
}

test("every surface stands in the list until the member puts one away", async () => {
  const { calls } = await runPage("chat", {
    "/objects/conversation$": () =>
      json({
        objects: [
          chatListRow(CONVO_ID, "Portal words"),
          chatListRow(ARRIVAL_ID, "Slack words", { surface: "slack", surface_label: "#ops" }),
        ],
        next_cursor: null,
      }),
  });
  const listReads = () => calls.filter((url) => url.includes("/objects/conversation"));
  await screen.findByRole("heading", { name: "Chat" });

  expect(listReads().every((url) => !url.includes("surface=") && !url.includes("portal="))).toBe(
    true,
  );
  expect(screen.getByText("Portal words")).toBeTruthy();
  expect(screen.getByText("Slack words")).toBeTruthy();

  const reads = listReads().length;
  await userEvent.click(screen.getByRole("button", { name: "Chats options" }));
  const slack = await screen.findByRole("menuitemcheckbox", { name: "Slack" });
  expect(slack.getAttribute("aria-checked")).toBe("true");
  await userEvent.click(slack);

  await vi.waitFor(() => expect(screen.queryByText("Slack words")).toBeNull());
  expect(screen.getByText("Portal words")).toBeTruthy();
  expect(listReads().length).toBe(reads);
  expect(screen.getByRole("menuitemcheckbox", { name: "iMessage" })).toBeTruthy();
  expect(localStorage.getItem("chat-hidden")).toBe("slack");
});

test("a surface the member put away is away on the next visit", async () => {
  localStorage.setItem("chat-hidden", "slack");
  await runPage("chat", {
    "/objects/conversation$": () =>
      json({
        objects: [
          chatListRow(CONVO_ID, "Portal words"),
          chatListRow(ARRIVAL_ID, "Slack words", { surface: "slack", surface_label: "#ops" }),
        ],
        next_cursor: null,
      }),
  });

  expect(await screen.findByText("Portal words")).toBeTruthy();
  expect(screen.queryByText("Slack words")).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Chats options" }));
  const ticked = async (label: string) =>
    (await screen.findByRole("menuitemcheckbox", { name: label })).getAttribute("aria-checked");
  expect(await ticked("Slack")).toBe("false");
  expect(await ticked("Terminal")).toBe("true");
  expect(await ticked("iMessage")).toBe("true");
});

test("the chat list runs its rows under the ladder this browser holds", async () => {
  const rows = [
    chatListRow(CONVO_ID, "Alpha"),
    chatListRow(ARRIVAL_ID, "Bravo", { agent_id: SECOND_ID, agent_name: "support" }),
    chatListRow(TURN_ID, "Charlie", { surface: "slack", surface_label: "#ops" }),
  ];
  localStorage.setItem("chat-ladder", "app");
  await runPage("chat", {
    "/objects/conversation$": () => json({ objects: rows, next_cursor: null }),
  });

  await screen.findByRole("heading", { name: "Chat" });
  expect(await screen.findByRole("heading", { name: "Assistant" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Support" })).toBeTruthy();

  expect(
    screen.getByRole("button", { name: "Chats options" }).className.split(/\s+/),
  ).toContain("bg-fill");

  await userEvent.click(screen.getByRole("button", { name: "Chats options" }));
  await userEvent.click(await screen.findByRole("menuitemradio", { name: "Recency" }));

  await vi.waitFor(() => expect(screen.queryByRole("heading", { name: "Support" })).toBeNull());
  expect(screen.getByRole("heading", { name: "Today" })).toBeTruthy();
  expect(screen.getByText("Alpha")).toBeTruthy();
  expect(screen.getByText("Charlie")).toBeTruthy();
  expect(localStorage.getItem("chat-ladder")).toBe("");
});

test("the list opens on the day-runs a conversation falls in, and skips the empty ones", async () => {
  const at = (days: number) => new Date(Date.now() - days * 86_400_000).toISOString();
  await runPage("chat", {
    "/objects/conversation$": () =>
      json({
        objects: [
          chatListRow(CONVO_ID, "This morning", { last_at: at(0) }),
          chatListRow(ARRIVAL_ID, "Earlier this week", { last_at: at(3) }),
          chatListRow(TURN_ID, "Last month", { last_at: at(20) }),
          chatListRow(SECOND_ID, "Long ago", { last_at: at(400) }),
        ],
        next_cursor: null,
      }),
  });

  const headings = async () =>
    (await screen.findAllByRole("heading", { level: 2 })).map((entry) => entry.textContent);
  expect(await headings()).toEqual([
    "Today",
    "Previous 7 days",
    "Previous 30 days",
    "Older",
  ]);

  const options = screen.getByRole("button", { name: "Chats options" });
  expect(options.textContent).toBe("");
  expect(options.className.split(/\s+/)).not.toContain("bg-fill");
});

test("a row from another surface trails the source it came in on, and a portal row trails none", async () => {
  await runPage(
    "chat",
    {
      "/objects/conversation$": () =>
        json({
          objects: [
            chatListRow(CONVO_ID, "Portal words"),
            chatListRow(ARRIVAL_ID, "Slack words", { surface: "slack", surface_label: "#ops" }),
            chatListRow(TURN_ID, "Terminal words", { surface: "ufo" }),
            chatListRow(SECOND_ID, "Meeting words", {
              agent_id: SECOND_ID,
              agent_name: "meetings",
            }),
          ],
          next_cursor: null,
        }),
    },
  );

  const row = async (title: string) =>
    (await screen.findByText(title)).closest("button") as HTMLElement;
  const slack = await row("Slack words");
  expect(slack.textContent).toContain("#ops");
  expect(slack.querySelectorAll("svg").length).toBe(1);

  const terminal = await row("Terminal words");
  expect(terminal.textContent).toContain("Terminal");

  const portal = await row("Portal words");
  expect(portal.querySelectorAll("svg").length).toBe(0);

  expect(portal.textContent).not.toContain(agentName(AGENT.name));
  expect((await row("Meeting words")).textContent).toContain("Meetings");
});

test("one page of the chat list gathers the listing's own pages up to its bound", async () => {
  const { calls } = await runPage("chat", {
    "/objects/conversation$": (url) => {
      const held = /cursor=walk-(\d+)/.exec(url);
      const page = held === null ? 1 : Number(held[1]) + 1;
      return json({
        objects: [chatListRow(CONVO_ID.slice(0, -2) + String(page).padStart(2, "0"), "Page " + page)],
        next_cursor: "walk-" + page,
      });
    },
  });

  await screen.findByRole("heading", { name: "Chat" });
  await vi.waitFor(() => expect(screen.getByText("Page 6")).toBeTruthy());
  expect(screen.getByText("Page 1")).toBeTruthy();
  expect(screen.queryByText("Page 7")).toBeNull();
  expect(calls.some((url) => url.includes("cursor=walk-5"))).toBe(true);
  expect(calls.some((url) => url.includes("cursor=walk-6"))).toBe(false);
  expect(await screen.findByRole("button", { name: "Older conversations" })).toBeTruthy();
});

test("the chat list stands over the entry that starts a conversation, and a send founds one", async () => {
  const { calls } = await runPage("chat", {
    "/objects/conversation$": () =>
      json({ objects: [chatListRow(CONVO_ID, "Warehouse restock")], next_cursor: null }),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: ARRIVAL_ID, title: "fresh words" }),
  });

  await screen.findByRole("heading", { name: "Chat" });
  expect(screen.getByText("Warehouse restock")).toBeTruthy();
  const box = await screen.findByLabelText("Ask UFO");
  expect(screen.getByRole("button", { name: "Send" })).toBeTruthy();

  await userEvent.type(box, "start something");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  await vi.waitFor(() =>
    expect(calls.some((url) => url.includes("/chat?conversation=new"))).toBe(true),
  );
});

test("the chat page answers an emptied track with its conversation list", async () => {
  const row = (id: string, title: string) => chatListRow(id, title);
  await runPage(
    "chat",
    {
      "/objects/conversation$": () =>
        json({ objects: [row(CONVO_ID, "Alpha"), row(ARRIVAL_ID, "Bravo")], next_cursor: null }),
      "/api/chats": () =>
        json({
          chats: [
            {
              conversation_id: CONVO_ID,
              agent_id: AGENT.id,
              agent_name: AGENT.name,
              title: "Alpha",
              surface: "web",
              last_at: "2026-08-01T09:00:00.000Z",
            },
          ],
        }),
      "/transcript": () => json({ messages: [] }),
    },
    { place: { opens: [CONVO_ID] } },
  );

  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 80));
  });
  expect(screen.queryByText("Bravo")).toBeNull();

  window.postMessage({ ufo: "place", place: { opens: [] } }, "*");

  await vi.waitFor(() => expect(screen.getByRole("heading", { name: "Chat" })).toBeTruthy());
  expect(screen.getByText("Bravo")).toBeTruthy();
  expect(screen.getByText("Alpha")).toBeTruthy();
});
