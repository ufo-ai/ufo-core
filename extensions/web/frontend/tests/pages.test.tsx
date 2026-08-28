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
  NO_ARTIFACTS,
  NO_RUNS,
  SECOND_ID,
  SITE_KIND,
  TURN_ID,
  json,
  objectIndex,
  wire,
  type Route,
} from "./harness";

const applicationLifecycle = await import("@/apps/lifecycle");
vi.doMock("@/apps/lifecycle", () => applicationLifecycle);
const lifecycle = (
  window as unknown as {
    __ufoApplicationLifecycle: { snapshot(): { blockingWork: number } };
  }
).__ufoApplicationLifecycle;

/** Each app's real page, run the way a built page runs: the extension's own TSX imported as a
 *  module against the kit `ufo/kit` resolves to, mounting itself through the bridge handshake
 *  against a fake shell on this same window — jsdom's `window.top` is the window itself, so the
 *  runtime's posts land on our listener, and every call the page's shimmed fetch tunnels is
 *  answered through the harness wire. */

const EXTENSIONS = join(import.meta.dirname, "..", "..", "..");

function pagePath(app: string): string {
  return join(EXTENSIONS, "app_" + app, "ufo_ext_app_" + app, "skills", "app-" + app + "-home", "app.tsx");
}

const INIT: AppInit = {
  member: { email: MEMBER.email, admin: true },
  agents: [AGENT],
  agentId: AGENT.id,
  place: {},
  portal: location.origin,
};

/** The shell's side of the bridge: `init` answers the page's `ready`, and each `call` is served
 *  through the wire handler captured before the page replaced fetch. A path the test left unwired
 *  answers as a refusal naming itself, so the screen that fails states which route was missing. */
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
  cleanups.push(shell(handler, init));
  const root = document.getElementById("root")!;
  await import(/* @vite-ignore */ pagePath(app));
  const { unmountApp } = await import("@/apps/shell");
  cleanups.push(() => unmountApp(root));
  return { calls };
}

const cleanups: (() => void)[] = [];
const nativeFetch = window.fetch;
const nativeEventSource = window.EventSource;

beforeEach(() => {
  vi.useRealTimers();
  document.body.innerHTML = '<div id="root"></div>';
});

afterEach(async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 50));
    for (const cleanup of cleanups.splice(0)) cleanup();
  });
  await vi.waitFor(() => expect(lifecycle.snapshot().blockingWork).toBe(0));
  window.fetch = nativeFetch;
  (window as { EventSource: typeof EventSource }).EventSource = nativeEventSource;
});

test("the radar page mounts and draws its empty feed under its own band", async () => {
  const { calls } = await runPage("radar", {
    "/objects/report": () => json({ objects: [] }),
  });
  expect(await screen.findByRole("heading", { name: "Radar" })).toBeTruthy();
  expect(await screen.findByRole("button", { name: "Rebuild entries" })).toBeTruthy();
  expect(await screen.findByText(NO_RUNS)).toBeTruthy();
  expect(calls.some((url) => url.includes("/api/agents"))).toBe(false);
  expect(calls.some((url) => url.includes("/objects/report"))).toBe(true);
});

test("page module resets reuse the installed lifecycle", async () => {
  await runPage("radar", {
    "/objects/report": () => json({ objects: [] }),
  });
  expect(await screen.findByRole("heading", { name: "Radar" })).toBeTruthy();

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

/** A page standing one step deeper than the shell's trail draws that step as its crumb, so where the
 *  member is reads the same inside the frame as the tab title says outside it. The crumb goes to the
 *  app's own address; shutting the page is the band's own act beside it. */
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

/** What a member told an app survives consolidation on their own page. The hourly pass collapses
 *  aged rows into one summary and stamps the originals superseded, so the rows themselves leave
 *  every band; a band drawing `fact` alone would then hold nothing, and the summary standing for
 *  them belongs to no other part of the page — the member's own words would be readable nowhere.
 *  An `episodic` row of the same band stays off it: recall offers a breadcrumb as a topic to open,
 *  and a document does not state the motion of a tool as one of its lines. */
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
      // The listing filters by class server-side and pages the answer, so each band asks for one
      // class at a time. A stub answering every class on one read would prove the opposite of what
      // ships: that a band can be crowded out by rows it never draws.
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

test("the artifacts page mounts and draws the empty shelf with its search", async () => {
  await runPage("artifacts", {
    "/objects/site": () => objectIndex(SITE_KIND, []),
    "/objects/artifact": () => json({ objects: [] }),
  });
  expect(await screen.findByRole("heading", { name: "Artifacts" })).toBeTruthy();
  expect(await screen.findByRole("searchbox", { name: "Search artifacts" })).toBeTruthy();
  expect(await screen.findByText(NO_ARTIFACTS)).toBeTruthy();
});

/** A page that ships filled in still has to mount: its placeholder is the shape the app rebuilds
 *  against, so a page that throws on mount is a page the app forks a broken copy of. Each of the
 *  three draws its own bands and the conversation list the kit hands it. */
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

  // Wait for the page to finish the bridge handshake and subscribe to place messages before
  // driving one, or the post races the mount and is lost.
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

  // Switching the open target re-reads only the target's transcript — never the whole
  // conversation listing, which the page already holds and the switch does not change.
  expect(listReads()).toBe(listedForA);
});

/** The listing holds conversations of other apps too, so the conversation's own app is not where
 *  this page stands. The crumb is the shell's, which is what keeps the band and the tab title naming
 *  one app, and it carries the address back to it. */
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
    // The listing is portal-filtered, so the page resolves the Slack conversation by address.
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

/** What the History act does to the page: it empties the track, and the page answers with the
 *  conversations it holds. The act is a navigation rather than a panel precisely because the page
 *  already draws this list — so this is the proof the portal needs to draw no second one. */
/** One row of the chat page's listing, as the conversation kind answers it: the member's own web
 *  chat unless the test says otherwise. */
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
    /* Now, so a row a test says nothing about the age of lands in Today rather than drifting into
       Older as the calendar moves past a literal. */
    last_at: new Date().toISOString(),
    ...extra,
  };
}

test("the Show set admits a surface into the list, and the read spans them all", async () => {
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

  // The read spans every surface the member can see; the set narrows the rows already in hand.
  expect(listReads().every((url) => !url.includes("surface=") && !url.includes("portal="))).toBe(
    true,
  );
  expect(screen.getByText("Portal words")).toBeTruthy();
  expect(screen.queryByText("Slack words")).toBeNull();

  const reads = listReads().length;
  await userEvent.click(screen.getByRole("button", { name: "Chats options" }));
  await userEvent.click(await screen.findByRole("menuitemcheckbox", { name: "Slack" }));

  await vi.waitFor(() => expect(screen.getByText("Slack words")).toBeTruthy());
  // A tick redraws the rows in hand rather than re-reading, and it leaves the menu standing so both
  // surfaces are named in one visit.
  expect(listReads().length).toBe(reads);
  expect(screen.getByRole("menuitemcheckbox", { name: "iMessage" })).toBeTruthy();
});

test("the chat list runs its rows under the ladder the address names", async () => {
  const rows = [
    chatListRow(CONVO_ID, "Alpha"),
    chatListRow(ARRIVAL_ID, "Bravo", { agent_id: SECOND_ID, agent_name: "support" }),
    chatListRow(TURN_ID, "Charlie", { surface: "slack", surface_label: "#ops" }),
  ];
  await runPage(
    "chat",
    { "/objects/conversation$": () => json({ objects: rows, next_cursor: null }) },
    { place: { group: "app", chip: "slack" } },
  );

  await screen.findByRole("heading", { name: "Chat" });
  expect(await screen.findByRole("heading", { name: "Assistant" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Support" })).toBeTruthy();

  // A ladder other than the one the list opens on fills the glyph in, so the bar says so without
  // being opened.
  expect(
    screen.getByRole("button", { name: "Chats options" }).className.split(/\s+/),
  ).toContain("bg-fill");

  // Recency is what the address carries nothing for, and it runs the rows under their day. The Show
  // set is a different axis and stands where it was.
  window.postMessage({ ufo: "place", place: { chip: "slack" } }, "*");
  await vi.waitFor(() => expect(screen.queryByRole("heading", { name: "Support" })).toBeNull());
  expect(screen.getByRole("heading", { name: "Today" })).toBeTruthy();
  expect(screen.getByText("Alpha")).toBeTruthy();
  expect(screen.getByText("Charlie")).toBeTruthy();
});

/** The day-runs are what the page opens on, and they are the runs the sidebar's own recency ladder
 *  drew before this list took them over. */
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
  // Named in a fixed order rather than the order the rows arrived in, and a run with nothing in it
  // is not drawn: Yesterday holds none of these.
  expect(await headings()).toEqual([
    "Today",
    "Previous 7 days",
    "Previous 30 days",
    "Older",
  ]);

  // The bar holds one glyph, and it is untinted while the list stands as it opens.
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
    { place: { chip: "slack,ufo" } },
  );

  const row = async (title: string) =>
    (await screen.findByText(title)).closest("button") as HTMLElement;
  const slack = await row("Slack words");
  expect(slack.textContent).toContain("#ops");
  expect(slack.querySelectorAll("svg").length).toBe(1);

  const terminal = await row("Terminal words");
  expect(terminal.textContent).toContain("Terminal");

  // The list is read in the portal, so a portal chat states no source.
  const portal = await row("Portal words");
  expect(portal.querySelectorAll("svg").length).toBe(0);

  // Nor does a row name the app whose page this is — that states the screen the member is on. An
  // app holding the conversation from somewhere else is named.
  expect(portal.textContent).not.toContain(agentName(AGENT.name));
  expect((await row("Meeting words")).textContent).toContain("Meetings");
});

test("one page of the chat list gathers the listing's own pages up to its bound", async () => {
  // A listing that never runs out: each page answers one row and the cursor to the next, keyed off
  // the cursor it was asked for, so the chain is the same whichever read starts the walk.
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
  // Six of the listing's pages stand as one page of this list, and the walk stops on its own bound
  // rather than on the listing.
  await vi.waitFor(() => expect(screen.getByText("Page 6")).toBeTruthy());
  expect(screen.getByText("Page 1")).toBeTruthy();
  expect(screen.queryByText("Page 7")).toBeNull();
  expect(calls.some((url) => url.includes("cursor=walk-5"))).toBe(true);
  expect(calls.some((url) => url.includes("cursor=walk-6"))).toBe(false);
  // The cursor the walk stopped at is what the step to the rest of the history carries.
  expect(await screen.findByRole("button", { name: "Older conversations" })).toBeTruthy();
});

test("the chat list stands over the entry that starts a conversation, and a send founds one", async () => {
  const { calls } = await runPage("chat", {
    "/objects/conversation$": () =>
      json({ objects: [chatListRow(CONVO_ID, "Warehouse restock")], next_cursor: null }),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: ARRIVAL_ID, title: "fresh words" }),
  });

  await screen.findByRole("heading", { name: "Chat" });
  // The conversations and the box are one screen: the member reads the list and writes the next
  // conversation without leaving for another.
  expect(screen.getByText("Warehouse restock")).toBeTruthy();
  const box = await screen.findByLabelText("Ask UFO");
  // The chat screen's own box, toolbar and all — not a control standing in for one.
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

  // The track names one conversation, so the page is not standing on the list.
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 80));
  });
  expect(screen.queryByText("Bravo")).toBeNull();

  window.postMessage({ ufo: "place", place: { opens: [] } }, "*");

  await vi.waitFor(() => expect(screen.getByRole("heading", { name: "Chat" })).toBeTruthy());
  expect(screen.getByText("Bravo")).toBeTruthy();
  expect(screen.getByText("Alpha")).toBeTruthy();
});
