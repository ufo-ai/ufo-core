import { readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { Portal } from "@/Portal";
import { agentName } from "@/lib/agentName";
import { SIGN_OUT_PATH } from "@/lib/api";
import { agentHash, chatHash } from "@/lib/route";
import { openAgents, placeAgent } from "@/lib/router";

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

/** mermaid's image shape prefetches `node.img` from any host during layout, which the markdown
 *  chokepoint cannot catch: only the page's own policy refuses a load it never minted an element for. */
test("the page tells the browser images load from this origin alone", () => {
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

const BUNDLE_CEILING_BYTES = 680_000;

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
      "Search",
      agentName(AGENT.name),
      "New app",
      "Channels",
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

test("a section heading is the fold and states which way it stands, and carries no other act", async () => {
  atPhoneWidth();
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const sidebar = await openWorkspaceColumn();
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

  const section = apps.parentElement;
  if (!section) throw new Error("the apps list stands in no section");
  expect(section.className).not.toContain("shrink-0");
  expect(apps.className).not.toContain("shrink-0");

  const scroller = apps.firstElementChild;
  if (!scroller) throw new Error("the apps list has no scrolling frame");
  expect(scroller.className).toContain("overflow-y-auto");
  expect(scroller.className).toContain("max-h-(--size-apps-open)");

  expect(within(sidebar).getByRole("button", { name: "Workspace" })).toBeTruthy();
});

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
    agentName(AGENT.name),
    "Pin " + agentName(AGENT.name),
    "Connections",
    "Workspace",
    "Theme",
    "Sign out",
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

test("the sidebar's foot states who is signed in and offers the way back out", async () => {
  atPhoneWidth();
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const sidebar = await openWorkspaceColumn();
  const foot = sidebar.querySelector("footer")!;
  expect(foot.querySelector("[data-slot=avatar-fallback]")!.textContent).toBe("M");
  expect(within(foot).getByText("member")).toBeTruthy();
  expect(within(foot).getByText(MEMBER.email)).toBeTruthy();

  const went: string[] = [];
  vi.stubGlobal("location", { ...window.location, assign: (to: string) => went.push(to) });
  await userEvent.click(within(foot).getByRole("button", { name: "Sign out" }));
  expect(went).toEqual([SIGN_OUT_PATH]);

  await userEvent.click(within(foot).getByRole("button", { name: "Theme" }));
  expect(await screen.findByRole("menuitemradio", { name: "System" })).toBeTruthy();
  expect(screen.getByRole("menuitemradio", { name: "Light" })).toBeTruthy();
  expect(screen.getByRole("menuitemradio", { name: "Dark" })).toBeTruthy();
});

async function documentationAct(host: string) {
  vi.stubGlobal("location", { ...window.location, hostname: host });
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(screen.getByRole("button", { name: MEMBER.email }));
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
