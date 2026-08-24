import { join } from "node:path";

import { act, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import type { AppInit } from "@/apps/runtime";
import { BASE, REFUSAL_HEADER, SESSION_FAULT_HEADER } from "@/lib/api";
import { agentHash } from "@/lib/route";

import {
  AGENT,
  ARRIVAL_ID,
  CHAT_ROW,
  CONVO_ID,
  MEMBER,
  NO_ARTIFACTS,
  NO_RUNS,
  NO_TASKS,
  NO_TRIGGERS,
  SECOND_ID,
  SITE_KIND,
  TASK_KIND,
  TRIGGER_KIND,
  TURN_ID,
  json,
  objectIndex,
  wire,
  type Route,
} from "./harness";

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
  await import(/* @vite-ignore */ pagePath(app));
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
  // The kit mounts each page on its own React root this harness cannot unmount, so drain the
  // page's pending bridge round-trips and their React work here — while the shell and fetch it
  // tunnels through still stand — rather than letting them fire after the test's jsdom is torn down
  // (which surfaces as an unhandled `window is not defined`).
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 50));
  });
  for (const cleanup of cleanups.splice(0)) cleanup();
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

test("the tasks page mounts and draws both of its listings", async () => {
  await runPage("tasks", {
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, []),
    "/objects/source_trigger": () => objectIndex(TRIGGER_KIND, []),
  });
  expect(await screen.findByRole("heading", { name: "Tasks" })).toBeTruthy();
  expect(await screen.findByText(NO_TASKS)).toBeTruthy();
  expect(await screen.findByText(NO_TRIGGERS)).toBeTruthy();
});

test("the wiki page mounts and draws the workspace article", async () => {
  await runPage("wiki", {
    "/objects/memory": () => json({ objects: [] }),
    "/objects/member": () => json({ objects: [] }),
  });
  expect(await screen.findByRole("heading", { name: "Wiki" })).toBeTruthy();
  expect(await screen.findByRole("heading", { name: "People" })).toBeTruthy();
  expect(await screen.findByText("Everyone in this workspace.")).toBeTruthy();
});

/** A page standing one step deeper than the shell's trail draws that step as its crumb, so where the
 *  member is reads the same inside the frame as the tab title says outside it. The crumb goes to the
 *  app's own address; shutting the page is the band's own act beside it. */
test("the wiki page heads one member under the crumb the shell handed it", async () => {
  const email = "colleague@example.com";
  await runPage(
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
  const conversationRow = (id: string, title: string) => ({
    name: id,
    agent_id: AGENT.id,
    agent_name: AGENT.name,
    title,
    surface: "web",
    last_at: "2026-08-01T09:00:00.000Z",
  });
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
    "/transcript": () => json({ messages: [], earlier: 0 }),
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

test("the chat page opens a Slack or terminal conversation read-only, not as unavailable", async () => {
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
  };
  const { calls } = await runPage("chat", {
    // The listing is portal-filtered, so a Slack conversation is never in it: the page resolves it
    // by address, and the resolve answers a read-only conversation rather than a repliable chat.
    "/objects/conversation": () => json({ objects: [], next_cursor: null }),
    "/api/chats": () => json({ chats: [], conversation: slackConversation }),
    "/transcript": () => json({ messages: [], earlier: 0 }),
  });

  await screen.findByRole("heading", { name: "Chat" });
  window.postMessage({ ufo: "place", place: { opens: [SLACK] } }, "*");

  // The read-only note stands, the miss never does, and the transcript is fetched by its address.
  expect(await screen.findByText(/Reply in Slack to continue it/)).toBeTruthy();
  expect(screen.queryByText("This conversation is not available here.")).toBeNull();
  expect(calls.some((url) => url.includes("transcript") && url.includes(SLACK))).toBe(true);
});
