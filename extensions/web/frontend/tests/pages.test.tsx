import { readFileSync } from "node:fs";
import { join } from "node:path";

import { screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { BASE, REFUSAL_HEADER, SESSION_FAULT_HEADER } from "@/lib/api";

import {
  AGENT,
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
  open: null,
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

async function runPage(app: string, routes: Record<string, Route>): Promise<void> {
  vi.resetModules();
  const { handler } = wire({ "/api/agents": () => json({ agents: [AGENT] }), ...routes });
  cleanups.push(shell(handler));
  const kit = await import("@/apps/kit");
  vi.stubGlobal("UfoAppKit", kit);
  new Function(kit.compile(pageSource(app)))();
}

const cleanups: (() => void)[] = [];
const nativeFetch = window.fetch;
const nativeEventSource = window.EventSource;

beforeEach(() => {
  vi.useRealTimers();
  document.body.innerHTML = '<div id="root"></div>';
});

afterEach(() => {
  for (const cleanup of cleanups.splice(0)) cleanup();
  window.fetch = nativeFetch;
  (window as { EventSource: typeof EventSource }).EventSource = nativeEventSource;
});

test("the radar page mounts and draws its empty feed under its own band", async () => {
  await runPage("radar", {
    "/workspace/radar": () => json({ runs: [] }),
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
    "/workspace/memory": () => json({ available: true, matches: [] }),
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
    "/workspace/artifacts": () => json({ artifacts: [] }),
  });
  expect(await screen.findByRole("heading", { name: "Artifacts" })).toBeTruthy();
  expect(await screen.findByRole("searchbox", { name: "Search artifacts" })).toBeTruthy();
  expect(await screen.findByText(NO_ARTIFACTS)).toBeTruthy();
});

test("the chat page mounts and draws the empty conversation list", async () => {
  await runPage("chat", {});
  expect(await screen.findByRole("heading", { name: "Chat" })).toBeTruthy();
  expect(await screen.findByText("No conversations yet.")).toBeTruthy();
});
