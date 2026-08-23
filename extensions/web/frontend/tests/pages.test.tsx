import { readFileSync } from "node:fs";
import { join } from "node:path";

import { act, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { BASE, REFUSAL_HEADER, SESSION_FAULT_HEADER } from "@/lib/api";

import {
  AGENT,
  ARRIVAL_ID,
  CONVO_ID,
  MEMBER,
  NO_ARTIFACTS,
  NO_RUNS,
  NO_TASKS,
  NO_TRIGGERS,
  SITE_KIND,
  TASK_KIND,
  TRIGGER_KIND,
  json,
  objectIndex,
  wire,
  type Route,
} from "./harness";

/** Each app's real page, run the way production runs it: the TSX read off the extension's own
 *  skill, compiled by the kit, executed against the `UfoAppKit` global, and mounted through the
 *  bridge handshake against a fake shell on this same window — jsdom's `window.top` is the window
 *  itself, so the runtime's posts land on our listener, and every call the page's shimmed fetch
 *  tunnels is answered through the harness wire. */

const EXTENSIONS = join(import.meta.dirname, "..", "..", "..");

function pageSource(app: string): string {
  return readFileSync(
    join(EXTENSIONS, "app_" + app, "ufo_ext_app_" + app, "skills", "app-" + app + "-home", "app.tsx"),
    "utf8",
  );
}

const INIT = {
  member: { email: MEMBER.email, admin: true },
  agentId: AGENT.id,
  place: {},
  portal: location.origin,
};

/** The shell's side of the bridge: `init` answers the page's `ready`, and each `call` is served
 *  through the wire handler captured before the page replaced fetch. A path the test left unwired
 *  answers as a refusal naming itself, so the screen that fails states which route was missing. */
function shell(handler: (url: string, init?: RequestInit) => Response | Promise<Response>): () => void {
  const listener = (event: MessageEvent) => {
    const message = event.data as { ufo?: string } | null;
    if (!message || typeof message.ufo !== "string") return;
    if (message.ufo === "ready") {
      window.postMessage({ ufo: "init", ...INIT }, "*");
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

async function runPage(app: string, routes: Record<string, Route>): Promise<{ calls: string[] }> {
  vi.resetModules();
  const { calls, handler } = wire({ "/api/agents": () => json({ agents: [AGENT] }), ...routes });
  cleanups.push(shell(handler));
  const kit = await import("@/apps/kit");
  vi.stubGlobal("UfoAppKit", kit);
  new Function(kit.compile(pageSource(app)))();
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
  await runPage("radar", {
    "/objects/report": () => json({ objects: [] }),
  });
  expect(await screen.findByRole("heading", { name: "Radar" })).toBeTruthy();
  expect(await screen.findByRole("button", { name: "Rebuild entries" })).toBeTruthy();
  expect(await screen.findByText(NO_RUNS)).toBeTruthy();
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
