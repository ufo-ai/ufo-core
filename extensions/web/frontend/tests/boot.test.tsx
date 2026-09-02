import { readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  SECOND_ID,
  atPhoneWidth,
  useStreamFake,
  wire,
} from "./harness";

const STATIC = join(import.meta.dirname, "..", "..", "ufo_ext_web", "static");
const builtPage = () => readFileSync(join(STATIC, "index.html"), "utf8");

const entryAsset = () => {
  const asset = /src="\/surface\/web\/static\/(assets\/[^"]+\.js)"/.exec(builtPage());
  if (!asset) throw new Error("the built page references no module");
  return asset[1];
};

beforeEach(() => {
  document.body.innerHTML = "";
  useStreamFake();
});

/** The workspace column, which the shell draws at a phone width alone: the drawer the hamburger
 *  opens holds it, drawn whole. Every act that moves the page shuts the drawer. */
async function openWorkspaceColumn(): Promise<HTMLElement> {
  await userEvent.click(await screen.findByRole("button", { name: "Menu" }));
  const drawer = await screen.findByRole("dialog");
  return within(drawer).getByRole("navigation", { name: "Workspace" });
}

test("the built page names a hashed module and stylesheet under this surface", () => {
  const page = builtPage();
  for (const ref of page.matchAll(/(?:src|href)="([^"]+)"/g)) {
    expect(ref[1].startsWith("/surface/web/")).toBe(true);
  }
  expect(/assets\/index-[A-Za-z0-9_-]+\.js/.test(page)).toBe(true);
  expect(/assets\/index-[A-Za-z0-9_-]+\.css/.test(page)).toBe(true);
  expect(page).toContain("<!doctype html>");
});

/** The markdown chokepoint refuses a foreign image element, but a renderer can fetch without
 *  minting an element — mermaid's image shape prefetches `node.img` from any host during layout.
 *  The page's own policy is the boundary a library cannot go around: the browser refuses every
 *  image load off this origin, however it was asked for. The portal draws no face and no company
 *  mark — an app page does, in its own document — so this one names no host at all, and a host
 *  added here buys a picture nothing draws at the cost of that refusal. */
test("the page tells the browser images load from this origin alone", () => {
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

test("a member whose seat was removed is told that, rather than an error to reload past", async () => {
  wire({
    "/api/agents": () =>
      new Response("workspace access was removed", {
        status: 403,
        headers: { "x-ufo-session-fault": "no-seat" },
      }),
  });
  render(<Portal />);

  expect(await screen.findByText("Workspace access disabled")).toBeTruthy();
  expect(screen.getByText(/An admin disabled this address/)).toBeTruthy();
  expect(
    screen.getByRole("link", { name: "Sign in with another email" }).getAttribute("href"),
  ).toBe(SIGN_OUT_PATH);
  expect(screen.queryByText("Error 403 — reload to retry.")).toBeNull();
});

test("a 403 that names no fault stays a failed boot the member can reload past", async () => {
  wire({ "/api/agents": () => new Response("forbidden", { status: 403 }) });
  render(<Portal />);

  expect(await screen.findByText("Error 403 — reload to retry.")).toBeTruthy();
  expect(screen.queryByText("Workspace access disabled")).toBeNull();
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

/** The shell at a desk width is the rail on the left: the mark that leads home, the act that opens a
 *  tab, search, one tile per tab home stands, the act that builds an app, the act that silences the
 *  track, and the workspace at its foot. The column is a glyph's own width, so every tile is a mark
 *  alone and the word it stands for is held at the pointer. */
test("the desk shell is a rail of marks, each holding its name at the pointer", async () => {
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const rail = await screen.findByRole("navigation", { name: "Tabs" });
  const names = () =>
    within(rail)
      .getAllByRole("button")
      .map((entry) => entry.getAttribute("aria-label"));
  await waitFor(() =>
    expect(names()).toEqual([
      "Home",
      "New tab",
      "Search",
      agentName(AGENT.name),
      "New app",
      "Mute sounds",
      "Workspace",
      MEMBER.email,
    ]),
  );

  const tile = within(rail).getByRole("button", { name: agentName(AGENT.name) });
  expect(tile.textContent).toBe("");
  fireEvent.focus(tile);
  expect((await screen.findByRole("tooltip")).textContent).toBe(agentName(AGENT.name));
});

/** The workspace column keeps one section, the apps, and the drawer that holds it at a phone width
 *  draws it whole. */
test("a section heading is the fold and states which way it stands, and carries no other act", async () => {
  atPhoneWidth();
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const sidebar = await openWorkspaceColumn();
  /* The band is the fold and states which way it stands. It opens no menu: a heading is a place
     before it is an act, and the act it does carry is the one a member does to a heading. */
  const band = within(sidebar).getByRole("button", { name: "Apps" });
  expect(band.getAttribute("aria-haspopup")).toBeNull();
  expect(band.getAttribute("aria-expanded")).toBe("true");
  const chevron = band.querySelector("svg");
  if (!chevron) throw new Error("Apps states no fold mark");
  expect(chevron.getAttribute("class")).not.toContain("opacity-0");
  expect(within(sidebar).queryByRole("button", { name: "Apps options" })).toBeNull();
});

test("the apps section yields its height rather than pushing the sidebar's foot off the screen", async () => {
  atPhoneWidth();
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const sidebar = await openWorkspaceColumn();
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

/** Until the member pins for themselves, the chat app stands pinned at the head of the index and
 *  every other app follows it by name — the one order a member can predict before they have put a
 *  row anywhere. */
test("until the member pins for themselves, the chat app leads and the rest stand by name", async () => {
  atPhoneWidth();
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

  const sidebar = await openWorkspaceColumn();
  const names = within(sidebar)
    .getAllByRole("button")
    .map((entry) => entry.getAttribute("aria-label") ?? entry.textContent);
  const at = (name: string) => names.indexOf(name);
  expect(at("Chat")).toBeGreaterThan(-1);
  expect(at("Assistant")).toBeGreaterThan(at("Chat"));
  expect(at("Wiki")).toBeGreaterThan(at("Assistant"));
});

/** A stored pin no live agent answers — an app since removed, or the old fixed reads — resolves
 *  to nothing rather than a row. */
test("a stored pin nothing answers draws no row", async () => {
  atPhoneWidth();
  localStorage.setItem("pinned-rows", "wiki\nradar");
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const sidebar = await openWorkspaceColumn();
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
    "New chat",
    "Create app",
    "Apps",
    /* The drawer is always drawn whole, so the app's row states its name and the pin act every row
       wears. */
    agentName(AGENT.name),
    "Pin " + agentName(AGENT.name),
    "Connectors",
    "Workspace",
    "Theme",
    "Sign out",
  ]);
});

test("the sidebar's foot states who is signed in and offers the way back out", async () => {
  atPhoneWidth();
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const sidebar = await openWorkspaceColumn();
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

