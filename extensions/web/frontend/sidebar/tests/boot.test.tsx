import { readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { Portal } from "@/Portal";
import { agentName } from "@/lib/agentName";
import { SIGN_OUT_PATH } from "@/lib/api";
import { chatHash } from "@/lib/route";

import {
  AGENT,
  MEMBER,
  atPhoneWidth,
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

/** The markdown chokepoint refuses a foreign image element, but a renderer can fetch without
 *  minting an element — mermaid's image shape prefetches `node.img` from any host during layout.
 *  The page's own policy is the boundary a library cannot go around: the browser refuses every
 *  image load off this origin, however it was asked for. */
test("the page tells the browser images load only from this origin", () => {
  const meta = /<meta http-equiv="Content-Security-Policy" content="([^"]+)">/.exec(builtPage());
  expect(meta?.[1]).toBe("img-src 'self' data:");
});

test("the page loads one script, which is the one this test runs", () => {
  const scripts = Array.from(builtPage().matchAll(/<script[^>]*\bsrc="([^"]+)"/g));
  expect(scripts.map((tag) => tag[1])).toEqual(["/surface/web/static/" + entryAsset()]);
});

const DIAGRAM_LIBRARIES = ["cytoscape", "roughjs", "d3-selection", "d3-scale", "elkjs"];

const BUNDLE_CEILING_BYTES = 1_400_000;

test("the one script carries no diagram library, and stays under its ceiling", () => {
  const bundle = readFileSync(join(STATIC, entryAsset()), "utf8");
  expect(DIAGRAM_LIBRARIES.filter((name) => bundle.includes(name))).toEqual([]);
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
  // The bearer behind this refusal is live, so the sign-in door would forward it straight back to
  // the workspace that just refused it. Another address is reached by clearing this one first.
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
    "Expand sidebar",
    "Ask assistant",
    "Apps",
    "New app",
    agentName(AGENT.name),
    "Chats",
    "Chats options",
    "Connectors",
    "Workspace",
    "Channels",
    "Theme",
    "Sign out",
  ]);
  expect(within(sidebar).getByText(MEMBER.email)).toBeTruthy();
});

test("a section heading folds its section, and holds its menu behind a mark drawn under the pointer", async () => {
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(screen.getByRole("button", { name: "Expand sidebar" }));
  const sidebar = screen.getByRole("navigation", { name: "Workspace" });
  for (const name of ["Apps", "Chats"]) {
    /* The band is the fold and states which way it stands. It opens no menu: a heading is a place
       before it is an act, and the act it does carry is the one a member does to a heading. */
    const band = within(sidebar).getByRole("button", { name });
    expect(band.getAttribute("aria-haspopup")).toBeNull();
    expect(band.getAttribute("aria-expanded")).toBe("true");
    const chevron = band.querySelector("svg");
    if (!chevron) throw new Error(name + " states no fold mark");
    expect(chevron.getAttribute("class")).not.toContain("opacity-0");
  }

  /* A menu is a second act on the same row, so it takes its own mark — held in the layout and drawn
     transparent, so nothing under the pointer moves as it arrives. Only the section that has one
     draws it: Apps carries its act as a row instead. */
  expect(within(sidebar).queryByRole("button", { name: "Apps options" })).toBeNull();
  const options = within(sidebar).getByRole("button", { name: "Chats options" });
  expect(options.getAttribute("aria-haspopup")).toBe("menu");
  expect(options.getAttribute("class")).toContain("opacity-0");
  expect(options.getAttribute("class")).toContain("group-hover/head:opacity-100");
  expect(options.getAttribute("class")).not.toContain("hidden");
});

test("the apps section yields its height rather than pushing the sidebar's foot off the screen", async () => {
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(screen.getByRole("button", { name: "Expand sidebar" }));
  const sidebar = screen.getByRole("navigation", { name: "Workspace" });
  const apps = within(sidebar).getByRole("navigation", { name: "Apps" });

  /* The nav scrolls nowhere, so a section held at its natural height would push the rows under it
     — Connectors, Workspace, and the way out — past the bottom edge on a short screen. The squeeze
     is spent inside the list, which caps and scrolls. */
  const section = apps.parentElement;
  if (!section) throw new Error("the apps list stands in no section");
  expect(section.className).not.toContain("shrink-0");
  expect(apps.className).not.toContain("shrink-0");

  const scroller = apps.firstElementChild;
  if (!scroller) throw new Error("the apps list has no scrolling frame");
  expect(scroller.className).toContain("overflow-y-auto");
  expect(scroller.className).toContain("max-h-(--size-apps-open)");

  // The foot is what must survive it.
  expect(within(sidebar).getByRole("button", { name: "Workspace" })).toBeTruthy();
});

test("the shell opens on the rail, and a sidebar the member widened stays widened", async () => {
  wire({});
  const first = render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(screen.getByRole("button", { name: "Expand sidebar" }));
  expect(screen.getByRole("button", { name: "Collapse sidebar" })).toBeTruthy();
  first.unmount();

  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  expect(screen.getByRole("button", { name: "Collapse sidebar" })).toBeTruthy();
});

/** A desk width draws the mark at the sidebar's head; a phone width keeps it on the bar, centred
test("until the member pins for themselves, the workspace's apps stand pinned, chat first", () => {
  wire({});
  const apps = [
    { id: SECOND_ID, name: "wiki", model: "auto", main: false, icon: "stele", app: "wiki" },
    {
      id: "3aa87d3e-8f10-4d40-bb9c-9e40f79c11aa",
      name: "chat",
      model: "auto",
      main: false,
      icon: "flange",
      app: "chat",
    },
  ];
  render(<App agents={[AGENT, ...apps]} member={MEMBER} onAgents={() => {}} />);

  const sidebar = screen.getByRole("navigation", { name: "Workspace" });
  const names = within(sidebar)
    .getAllByRole("button")
    .map((entry) => entry.getAttribute("aria-label") ?? entry.textContent);
  const chat = names.indexOf("Chat");
  const wiki = names.indexOf("Wiki");
  expect(chat).toBeGreaterThan(-1);
  expect(wiki).toBeGreaterThan(chat);
  expect(names).not.toContain("Assistant");
});

/** A stored pin no live agent answers — an app since removed, or the old fixed reads — resolves
 *  to nothing rather than a row. */
test("a stored pin nothing answers draws no row", () => {
  localStorage.setItem("pinned-rows", "wiki\nradar");
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const sidebar = screen.getByRole("navigation", { name: "Workspace" });
  const names = within(sidebar)
    .getAllByRole("button")
    .map((entry) => entry.getAttribute("aria-label") ?? entry.textContent);
  expect(names).not.toContain("Wiki");
  expect(names).not.toContain("Radar");
});

/** The bar's mark stands between the hamburger and the account. It is centred out of the row —
 *  absolutely placed — so the row it stands over keeps one line whatever the mark's own width. */
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
    "Ask assistant",
    "Apps",
    "New app",
    /* The drawer is always drawn whole, so the app's row states its name and the pin act every row
       wears. */
    agentName(AGENT.name),
    "Pin " + agentName(AGENT.name),
    "Chats",
    "Chats options",
    "Connectors",
    "Workspace",
    "Channels",
    "Theme",
    "Sign out",
  ]);
});

test("the sidebar's foot states who is signed in and offers the way back out", async () => {
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const sidebar = screen.getByRole("navigation", { name: "Workspace" });
  const foot = sidebar.querySelector("footer")!;
  expect(foot.querySelector("[data-slot=avatar-fallback]")!.textContent).toBe("M");
  expect(within(foot).getByText(MEMBER.email)).toBeTruthy();
  expect(within(foot).getByText("Member")).toBeTruthy();

  // The sign-in door forwards a browser that already holds a session, so this is the act that
  // reaches the form: it clears the cookies on the host that bound them and lands there.
  const went: string[] = [];
  vi.stubGlobal("location", { ...window.location, assign: (to: string) => went.push(to) });
  await userEvent.click(within(foot).getByRole("button", { name: "Sign out" }));
  expect(went).toEqual([SIGN_OUT_PATH]);

  await userEvent.click(within(foot).getByRole("button", { name: "Theme" }));
  expect(await screen.findByRole("menuitemradio", { name: "System" })).toBeTruthy();
  expect(screen.getByRole("menuitemradio", { name: "Light" })).toBeTruthy();
  expect(screen.getByRole("menuitemradio", { name: "Dark" })).toBeTruthy();
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

