import { readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { Portal } from "@/Portal";
import { SIGN_OUT_PATH } from "@/lib/api";
import { agentHash, chatHash } from "@/lib/route";
import { openAgents, placeAgent } from "@/lib/router";

import {
  AGENT,
  MEMBER,
  atPhoneWidth,
  json,
  useStreamFake,
  wire,
} from "./harness";

const STATIC = join(import.meta.dirname, "..", "..", "..", "ufo_ext_web", "static");
const builtPage = () => readFileSync(join(STATIC, "sidebar.html"), "utf8");

const entryAsset = () => {
  const asset = /src="\/surface\/web\/static\/(assets\/[^"]+\.js)"/.exec(builtPage());
  if (!asset) throw new Error("the built page references no module");
  return asset[1];
};

beforeEach(() => {
  document.body.innerHTML = "";
  useStreamFake();
});

test("the built page names a hashed module and stylesheet under this surface", () => {
  const page = builtPage();
  for (const ref of page.matchAll(/(?:src|href)="([^"]+)"/g)) {
    expect(ref[1].startsWith("/surface/web/")).toBe(true);
  }
  expect(/assets\/sidebar-[A-Za-z0-9_-]+\.js/.test(page)).toBe(true);
  expect(/assets\/sidebar-[A-Za-z0-9_-]+\.css/.test(page)).toBe(true);
  expect(page).toContain("<!doctype html>");
});

/** mermaid's image shape prefetches `node.img` from any host during layout, which the markdown
 *  chokepoint cannot catch: only the page's own policy refuses a load it never minted an element for. */
test("the page tells the browser images load only from this origin", () => {
  const meta = /<meta http-equiv="Content-Security-Policy" content="([^"]+)">/.exec(builtPage());
  expect(meta?.[1]).toBe(
    "img-src 'self' data: https://www.google.com/s2/favicons https://*.gstatic.com",
  );
});

test("the page loads one script, which is the one this test runs", () => {
  const scripts = Array.from(builtPage().matchAll(/<script[^>]*\bsrc="([^"]+)"/g));
  expect(scripts.map((tag) => tag[1])).toEqual(["/surface/web/static/" + entryAsset()]);
});

const DIAGRAM_LIBRARIES = ["cytoscape", "roughjs", "d3-selection", "d3-scale", "elkjs"];

/** Every route's pane is a chunk of its own, so the markdown stack the transcript renders through is
 *  what a member who never opens a conversation does not download. */
const TRANSCRIPT_LIBRARIES = ["micromark", "mdast", "streamdown"];

/** The resize handle every pane's sheet is dragged by rides the boot script, since a pane splits
 *  on any route: 35KB of the headroom below went to it. */
const BUNDLE_CEILING_BYTES = 715_000;

test("the one script carries no diagram or transcript library, and stays under its ceiling", () => {
  const bundle = readFileSync(join(STATIC, entryAsset()), "utf8");
  expect(DIAGRAM_LIBRARIES.filter((name) => bundle.includes(name))).toEqual([]);
  expect(TRANSCRIPT_LIBRARIES.filter((name) => bundle.includes(name))).toEqual([]);
  expect(bundle.length).toBeLessThan(BUNDLE_CEILING_BYTES);
});

test("the built bundle mounts into the served page and asks for the session's agents", async () => {
  const asked: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      asked.push(url);
      return new Response("unauthorized", { status: 401 });
    }),
  );
  const went: string[] = [];
  vi.stubGlobal("location", { ...window.location, hash: "", assign: (to: string) => went.push(to) });
  document.body.innerHTML = builtPage().replace(/<script[\s\S]*?<\/script>/g, "");

  await import(pathToFileURL(join(STATIC, entryAsset())).href);
  await waitFor(() => expect(asked).toContain("/surface/web/api/agents"));
  await waitFor(() => expect(went).toEqual(["/login"]));
  vi.unstubAllGlobals();
});

test("a session that ends under the open page goes straight to the sign-in page", async () => {
  const went: string[] = [];
  vi.stubGlobal("location", { ...window.location, hash: "", assign: (to: string) => went.push(to) });
  wire({ "/api/agents": () => new Response("unauthorized", { status: 401 }) });
  render(<Portal />);

  await waitFor(() => expect(went).toEqual(["/login"]));
  expect(screen.queryByText("Session ended")).toBeNull();
  vi.unstubAllGlobals();
});

test("a session that ends over a conversation carries it onto the sign-in page", async () => {
  const went: string[] = [];
  const conversation = "8f14e45f-ceea-467a-9b3e-1a2b3c4d5e6f";
  vi.stubGlobal("location", {
    ...window.location,
    hash: chatHash(conversation),
    assign: (to: string) => went.push(to),
  });
  wire({ "/api/agents": () => new Response("unauthorized", { status: 401 }) });
  render(<Portal />);

  await waitFor(() => expect(went).toEqual(["/login?c=" + conversation]));
  vi.unstubAllGlobals();
});

test("a live session whose email holds no member row is told that, not to sign in again", async () => {
  wire({
    "/api/agents": () =>
      new Response("no member with this email in this workspace", {
        status: 401,
        headers: { "x-ufo-session-fault": "no-member" },
      }),
  });
  render(<Portal />);

  expect(await screen.findByText("Not a member of this workspace")).toBeTruthy();
  expect(screen.getByText(/An admin has to add the address/)).toBeTruthy();
  expect(
    screen.getByRole("link", { name: "Sign in with another email" }).getAttribute("href"),
  ).toBe(SIGN_OUT_PATH);
  expect(screen.queryByText("Session ended")).toBeNull();
});

test("a boot that fails on the server states the status and renders no shell", async () => {
  wire({ "/api/agents": () => new Response("boom", { status: 502 }) });
  render(<Portal />);

  expect(await screen.findByText("Error 502 — reload to retry.")).toBeTruthy();
  expect(screen.queryByRole("tablist")).toBeNull();
});

test("a boot whose network fails says so rather than sending a live session to sign in", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      throw new Error("offline");
    }),
  );
  render(<Portal />);
  expect(await screen.findByText("Network error — try again.")).toBeTruthy();
});

test("the sidebar names the shell's destinations and states the member at its foot", () => {
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const sidebar = screen.getByRole("navigation", { name: "Workspace" });
  const names = within(sidebar)
    .getAllByRole("button")
    .map((entry) => entry.getAttribute("aria-label") ?? entry.textContent);
  expect(names).toEqual([
    "Search",
    "Collapse sidebar",
    "New chat\u21e7\u2318O",
    "Automations",
    "Radar",
    "Artifacts",
    "Connections",
    "Settings",
    "New chat",
    "Chats options",
    MEMBER.email,
  ]);
  const head = sidebar.firstElementChild as HTMLElement;
  expect(
    within(head)
      .getAllByRole("button")
      .map((entry) => entry.getAttribute("aria-label")),
  ).toEqual(["Search", "Collapse sidebar"]);
  expect(within(head).getByRole("img", { name: "ufo" })).toBeTruthy();
  expect(within(sidebar).getByText(MEMBER.email)).toBeTruthy();
});

test("every nav row lands on its own portal view", async () => {
  wire({ "/transcript": () => json({ messages: [] }), "/homepage": () => json({ state: "none" }) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const rail = within(screen.getByRole("navigation", { name: "Workspace" }));
  await userEvent.click(rail.getByRole("button", { name: "Automations" }));
  expect(location.hash).toBe("#/automations");

  await userEvent.click(rail.getByRole("button", { name: "Radar" }));
  await waitFor(() => expect(location.hash).toBe("#/radar"));

  await userEvent.click(rail.getByRole("button", { name: "Artifacts" }));
  expect(location.hash).toBe("#/artifacts");

  await userEvent.click(rail.getByRole("button", { name: "Connections" }));
  expect(location.hash).toBe("#/connectors");
});

test("a section heading names its list and holds its menu behind a mark drawn under the pointer", async () => {
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const sidebar = screen.getByRole("navigation", { name: "Workspace" });
  expect(within(sidebar).getByRole("heading", { name: "Chats" })).toBeTruthy();
  expect(within(sidebar).queryByRole("button", { name: "Chats" })).toBeNull();

  const compose = within(sidebar).getAllByRole("button", { name: "New chat" }).at(-1)!;
  expect(compose.getAttribute("class")).toContain("group-hover/head:opacity-100");

  const options = within(sidebar).getByRole("button", { name: "Chats options" });
  expect(options.getAttribute("aria-haspopup")).toBe("menu");
  expect(options.getAttribute("class")).toContain("opacity-0");
  expect(options.getAttribute("class")).toContain("group-hover/head:opacity-100");
  expect(options.getAttribute("class")).not.toContain("hidden");
});

test("the shell opens with the sidebar open, and a sidebar the member folded stays folded", async () => {
  wire({});
  const first = render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));
  expect(screen.getByRole("button", { name: "Expand sidebar" })).toBeTruthy();
  first.unmount();

  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  expect(screen.getByRole("button", { name: "Expand sidebar" })).toBeTruthy();
});

test("the bar's mark stands at a phone width too, centred out of the row", () => {
  atPhoneWidth();
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const mark = screen.getByRole("img", { name: "ufo" });
  const stand = mark.closest("button")!;
  expect(stand.className).toContain("absolute");
  expect(stand.className).toContain("start-1/2");
  expect(mark.closest("header")!.className).toContain("relative");
});

test("the account at a phone width offers the way back out", async () => {
  atPhoneWidth();
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(screen.getByRole("button", { name: MEMBER.email }));
  const went: string[] = [];
  vi.stubGlobal("location", { ...window.location, assign: (to: string) => went.push(to) });
  await userEvent.click(await screen.findByRole("menuitem", { name: "Sign out" }));
  expect(went).toEqual([SIGN_OUT_PATH]);
});

test("the menu drawer holds the whole sidebar and the act that starts a conversation", async () => {
  atPhoneWidth();
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(screen.queryByRole("navigation", { name: "Workspace" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Menu" }));

  const drawer = await screen.findByRole("dialog");
  const sidebar = within(drawer).getByRole("navigation", { name: "Workspace" });
  const names = within(sidebar)
    .getAllByRole("button")
    .map((entry) => entry.getAttribute("aria-label") ?? entry.textContent);
  expect(names).toEqual([
    "Search",
    "Collapse sidebar",
    "New chat\u21e7\u2318O",
    "Automations",
    "Radar",
    "Artifacts",
    "Connections",
    "Settings",
    "New chat",
    "Chats options",
    MEMBER.email,
  ]);
});

test("the drawer rides an address the screen replaces, and shuts when the member moves", async () => {
  atPhoneWidth();
  wire({});
  location.hash = agentHash(AGENT.id);
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(screen.getByRole("button", { name: "Menu" }));
  expect(await screen.findByRole("dialog")).toBeTruthy();

  act(() => placeAgent({ q: "notes" }, "replace"));
  expect(screen.getByRole("dialog")).toBeTruthy();

  act(() => openAgents());
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

test("the account menu inside the menu drawer is drawn in the drawer, acts and flyout alike", async () => {
  atPhoneWidth();
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(screen.getByRole("button", { name: "Menu" }));
  const drawer = await screen.findByRole("dialog");
  const sidebar = within(drawer).getByRole("navigation", { name: "Workspace" });

  await userEvent.click(within(sidebar).getByRole("button", { name: MEMBER.email }));
  const acts = await screen.findByRole("menu");
  expect(drawer.contains(acts)).toBe(true);
  expect(within(acts).queryByRole("menuitem", { name: "Settings" })).toBeNull();
  expect(within(acts).getByRole("menuitem", { name: "Sign out" })).toBeTruthy();

  await userEvent.click(within(acts).getByRole("menuitem", { name: "Theme" }));
  expect(drawer.contains(await screen.findByRole("menuitemradio", { name: "System" }))).toBe(true);
});

test("the sidebar's foot states who is signed in and holds the account's acts in one menu", async () => {
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const sidebar = screen.getByRole("navigation", { name: "Workspace" });
  const foot = sidebar.querySelector("footer")!;
  expect(foot.querySelector("[data-slot=avatar-fallback]")!.textContent).toBe("M");
  expect(within(foot).getByText(MEMBER.email)).toBeTruthy();

  await userEvent.click(within(foot).getByRole("button", { name: MEMBER.email }));
  expect(await screen.findByRole("menuitem", { name: "Theme" })).toBeTruthy();
  expect(screen.queryByRole("menuitem", { name: "Settings" })).toBeNull();

  await userEvent.click(screen.getByRole("menuitem", { name: "Theme" }));
  expect(await screen.findByRole("menuitemradio", { name: "System" })).toBeTruthy();
  expect(screen.getByRole("menuitemradio", { name: "Light" })).toBeTruthy();
  expect(screen.getByRole("menuitemradio", { name: "Dark" })).toBeTruthy();

  const went: string[] = [];
  vi.stubGlobal("location", { ...window.location, assign: (to: string) => went.push(to) });
  await userEvent.click(screen.getByRole("menuitem", { name: "Sign out" }));
  expect(went).toEqual([SIGN_OUT_PATH]);
});

async function documentationAct(host: string) {
  vi.stubGlobal("location", { ...window.location, hostname: host });
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const sidebar = screen.getByRole("navigation", { name: "Workspace" });
  const foot = sidebar.querySelector("footer")!;
  await userEvent.click(within(foot).getByRole("button", { name: MEMBER.email }));
  return await screen.findByRole("menuitem", { name: "Documentation" });
}

test("a page served on the testing deploy opens the testing documentation", async () => {
  const docs = await documentationAct("app.testing.ufo.ai");
  expect(docs.getAttribute("href")).toBe("https://testing.ufo.ai/docs/");
});

test("the account's acts open the documentation in a tab of its own, over the divider above Theme", async () => {
  const docs = await documentationAct("app.ufo.ai");
  expect(docs.getAttribute("href")).toBe("https://ufo.ai/docs/");
  expect(docs.getAttribute("target")).toBe("_blank");
  expect(docs.getAttribute("rel")).toBe("noopener noreferrer");

  const menu = screen.getByRole("menu");
  const rows = [...menu.children];
  const divider = menu.querySelector("[data-slot=dropdown-menu-separator]")!;
  const theme = screen.getByRole("menuitem", { name: "Theme" });
  expect(rows.indexOf(docs)).toBeLessThan(rows.indexOf(divider));
  expect(rows.indexOf(divider)).toBeLessThan(rows.indexOf(theme));
});

test("a boot whose body is not json states the network fault, not a 200 error", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("<html>", { status: 200 })),
  );
  render(<Portal />);
  expect(await screen.findByText("Network error — try again.")).toBeTruthy();
  expect(screen.queryByText(/Error 200/)).toBeNull();
});

test("the entry refuses to mount a page with no root rather than doing nothing", async () => {
  document.body.innerHTML = "";
  await expect(import("@/main")).rejects.toThrow("the portal page has no #root to mount into");
});

test("a boot still reading says Loading… rather than rendering an empty page", () => {
  vi.stubGlobal("fetch", () => new Promise(() => {}));
  render(<Portal />);

  expect(screen.getByText("Loading…")).toBeTruthy();
});
