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
import { webAudienceLabel } from "@/lib/audience";

import {
  refusedNotice,
  ADMIN_AGENT,
  AGENT,
  MEMBER,
  type Route,
  SECOND_ID,
  atPhoneWidth,
  json,
  pageFits,
  pick,
  pressRow,
  tableFloors,
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

  expect(await screen.findByText("Workspace access removed")).toBeTruthy();
  expect(screen.getByText(/An admin removed your seat/)).toBeTruthy();
  expect(
    screen.getByRole("link", { name: "Sign in with another email" }).getAttribute("href"),
  ).toBe(SIGN_OUT_PATH);
  expect(screen.queryByText("Error 403 — reload to retry.")).toBeNull();
});

test("a 403 that names no fault stays a failed boot the member can reload past", async () => {
  wire({ "/api/agents": () => new Response("forbidden", { status: 403 }) });
  render(<Portal />);

  expect(await screen.findByText("Error 403 — reload to retry.")).toBeTruthy();
  expect(screen.queryByText("Workspace access removed")).toBeNull();
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

test("an admin is offered administration, which reads the admin projection", async () => {
  atPhoneWidth();
  wire({
    "/api/admin": () =>
      json({
        agents: [ADMIN_AGENT],
        members: [{ id: "m1", email: "member@example.com", admin: true, seated: true }],
        caps: [],
        deploy: { sandbox_internet: true, extensions: [] },
      }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={{ ...MEMBER, admin: true }} onAgents={() => {}} />);

  const foot = within(await openWorkspaceColumn());
  await userEvent.click(foot.getByRole("button", { name: "Administration" }));
  expect(await screen.findByText("Seated")).toBeTruthy();
  expect(screen.getByText("Every member")).toBeTruthy();
  expect(screen.getByText("No spend caps are set.")).toBeTruthy();
  expect(location.hash).toBe("#/admin");
  expect(document.querySelectorAll("h1").length).toBe(1);
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
  expect(screen.queryByRole("button", { name: "Administration" })).toBeNull();

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

const SECOND_AGENT = {
  ...ADMIN_AGENT,
  id: SECOND_ID,
  name: "second",
  main: false,
  installations: ["slack", "ufo"],
};

/** The web-access acts as the member object projects them on one member row: the grant, and the
 *  revoke with the sentence it states before it commits. */
const WEB_ACCESS = (action: string, label: string, confirm?: string) => ({
  name: action,
  description: "Change who reaches the agent on the web.",
  input_schema: { properties: {} },
  call: { kind: "member", action, name: "m1", input: {} },
  label,
  ...(confirm ? { confirm } : {}),
});

const ADMIN_MEMBER = {
  id: "m1",
  email: "member@example.com",
  admin: true,
  seated: true,
  actions: [
    WEB_ACCESS("grant_web_access", "Grant web access"),
    WEB_ACCESS("revoke_web_access", "Revoke web access", "Their access to this app ends."),
  ],
};

const ADMIN_TABLES = {
  agents: [SECOND_AGENT],
  members: [ADMIN_MEMBER],
  caps: [
    {
      scope: "workspace",
      subject: null,
      window_seconds: 3600,
      limit_micro_usd: 4_000,
      on_breach: "warn",
    },
  ],
  deploy: {
    sandbox_internet: true,
    extensions: [{ name: "web", version: "0.1.0", sandbox_internet: false }],
  },
};

/** The administration screen, opened at its own address: what these tests read is the screen, and
 *  the control that reaches it is the workspace column's, held by the drawer at a phone width. */
function administration(routes: Record<string, Route>) {
  wire({ "/transcript": () => json({ messages: [] }), ...routes });
  location.hash = "#/admin";
  render(
    <App
      agents={[SECOND_AGENT]}
      member={{ ...MEMBER, admin: true }}
      onAgents={() => {}}
    />,
  );
}

/** Revoking declares a confirm, so it arms on the first press and commits on the second, as every
 *  other destructive act in the portal does. */
async function revoke(record: HTMLElement): Promise<void> {
  await userEvent.click(within(record).getByRole("button", { name: "Revoke web access" }));
  await userEvent.click(within(record).getByRole("button", { name: "Confirm revoke web access" }));
}

/** The tracks are fixed pixels, so a table declaring more of them than the desktop leaves holds its
 *  width and the column scrolls sideways — and the act a row is pressed by is what falls off the
 *  right. Every table administration draws has to fit the page it is drawn on. */
test("every administration table fits the desktop page it is read on", async () => {
  administration({ "/api/admin": () => json(ADMIN_TABLES) });
  await screen.findByText("Second");

  expect(tableFloors()).toEqual([
    "calc(2 * var(--size-fact-column) + 2 * var(--size-prose-column) + 1 * var(--size-act))",
    "calc(2 * var(--size-fact-column) + 2 * var(--size-prose-column) + 0 * var(--size-act))",
    "calc(4 * var(--size-fact-column) + 1 * var(--size-prose-column) + 0 * var(--size-act))",
    "calc(2 * var(--size-fact-column) + 1 * var(--size-prose-column) + 0 * var(--size-act))",
  ]);
  for (const floor of tableFloors()) expect(pageFits(floor)).toBe(true);
});

/** The audience acts are the member object's own, projected on each member row, so no fixed track
 *  holds them at the pitch a row is read at. They stand in the agent's own record instead, behind
 *  the member they change, with the surfaces the row no longer states. */
test("an agent's record carries its surfaces and a member's web access acts, and the row does not", async () => {
  const posted: unknown[] = [];
  const lanes: string[] = [];
  const { calls } = wire({
    "/api/admin": () => json({ ...ADMIN_TABLES, caps: [] }),
    "/actions/member/": (url, init) => {
      lanes.push(url);
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Granted." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/admin";
  render(
    <App
      agents={[SECOND_AGENT]}
      member={{ ...MEMBER, admin: true }}
      onAgents={() => {}}
    />,
  );

  await screen.findByText("Second");
  expect(screen.queryByRole("combobox", { name: "Web Access Member" })).toBeNull();
  expect(screen.queryByText("Slack, Terminal")).toBeNull();

  await pressRow("Second");
  const record = await screen.findByRole("dialog", { name: "Second" });
  expect(within(record).getByText("Surfaces")).toBeTruthy();
  expect(within(record).getByText("Slack, Terminal")).toBeTruthy();

  await pick("Web Access Member", "member@example.com");
  await userEvent.click(within(record).getByRole("button", { name: "Grant web access" }));
  await waitFor(() => expect(posted.length).toBe(1));

  await revoke(record);
  await waitFor(() => expect(posted.length).toBe(2));

  expect(posted).toEqual([{}, {}]);
  expect(lanes).toEqual([
    "/surface/web/agents/" + SECOND_ID + "/actions/member/m1/grant_web_access",
    "/surface/web/agents/" + SECOND_ID + "/actions/member/m1/revoke_web_access",
  ]);
  expect(calls.filter((url) => url.includes("/api/admin")).length).toBe(3);
});

async function grantForm(): Promise<HTMLElement> {
  await pressRow("Second");
  return await screen.findByRole("dialog", { name: "Second" });
}

/** The acts belong to a member row, so none is drawn until the admin names the member: a press with
 *  nobody picked has nothing to address. */
test("no web access act is drawn until a member is picked", async () => {
  const posted: unknown[] = [];
  await administration({
    "/api/admin": () => json({ ...ADMIN_TABLES, caps: [] }),
    "/actions/member/": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Granted." });
    },
  });
  const record = await grantForm();

  expect(within(record).getByRole("combobox", { name: "Web Access Member" })).toBeTruthy();
  expect(within(record).queryByRole("button", { name: "Grant web access" })).toBeNull();
  expect(within(record).queryByRole("button", { name: "Revoke web access" })).toBeNull();

  await pick("Web Access Member", "member@example.com");
  expect(within(record).getByRole("button", { name: "Grant web access" })).toBeTruthy();
  await userEvent.click(within(record).getByRole("button", { name: "Revoke web access" }));
  expect(within(record).getByText("Their access to this app ends.")).toBeTruthy();
  expect(posted).toEqual([]);
});

const GRANTED_AGENT = {
  ...SECOND_AGENT,
  id: "44444444-4444-4444-8444-444444444444",
  name: "granted",
  web_audience: ["reader@example.com"],
};

/** `audience.ts` holds the one spelling of who reaches an agent on the web, so the row, the record,
 *  and every screen outside administration read one agent's audience the same way. */
test("the row and the record spell a web audience the way the one audience map does", async () => {
  administration({
    "/api/admin": () =>
      json({ ...ADMIN_TABLES, agents: [SECOND_AGENT, GRANTED_AGENT], members: [], caps: [] }),
  });
  await screen.findByText("Second");

  expect(screen.getByText(webAudienceLabel(false, []))).toBeTruthy();
  expect(screen.getByText(webAudienceLabel(false, GRANTED_AGENT.web_audience))).toBeTruthy();

  const record = await grantForm();
  expect(within(record).getByText(webAudienceLabel(false, []))).toBeTruthy();
});

/** The table is the root of the path, so a second app row leads there rather than stacking on the
 *  one the member walked past — the rule every other index in the portal takes from `opened`. */
test("a second app row takes the first record's place, and closing it leaves the table", async () => {
  administration({
    "/api/admin": () => json({ ...ADMIN_TABLES, agents: [ADMIN_AGENT, SECOND_AGENT], members: [] }),
  });

  await pressRow("Assistant");
  expect(await screen.findByRole("dialog", { name: "Assistant" })).toBeTruthy();

  await pressRow("Second");

  const second = await screen.findByRole("dialog", { name: "Second" });
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Assistant" })).toBeNull());

  await userEvent.click(within(second).getByRole("button", { name: "Close" }));

  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Second" })).toBeNull());
  expect(screen.getByText("Second")).toBeTruthy();
});

/** The main agent answers every member, so its record offers no address to grant. */
test("the main agent's record states its audience and carries no grant form", async () => {
  administration({
    "/api/admin": () => json({ ...ADMIN_TABLES, agents: [ADMIN_AGENT] }),
  });
  await pressRow("Assistant");

  const record = await screen.findByRole("dialog", { name: "Assistant" });
  expect(within(record).getByText("Every member")).toBeTruthy();
  expect(within(record).queryByRole("combobox", { name: "Web Access Member" })).toBeNull();
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

test("a failed administration read stays inside the scrolling frame the view owns", async () => {
  wire({
    "/api/admin": () => new Response("no", { status: 500 }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/admin";
  render(<App agents={[AGENT]} member={{ ...MEMBER, admin: true }} onAgents={() => {}} />);

  const message = await screen.findByText("Error 500 — reload to retry.");
  expect(message.closest("main")).not.toBeNull();
});

test("a boot still reading says Loading… rather than rendering an empty page", () => {
  vi.stubGlobal("fetch", () => new Promise(() => {}));
  render(<Portal />);

  expect(screen.getByText("Loading…")).toBeTruthy();
});

test("the admin loading arm keeps the padded frame its other arms own", async () => {
  wire({
    "/api/admin": () => new Promise<Response>(() => {}),
  });
  location.hash = "#/admin";
  render(<App agents={[AGENT]} member={{ ...MEMBER, admin: true }} onAgents={() => {}} />);

  const loading = await screen.findByTestId("admin-loading");
  expect(loading.closest("main")).not.toBeNull();
});

/** A refusal has no field to answer it, so it stands in the record the act was raised in — with
 *  the member still picked — and the page behind it keeps reading. */
test("a refused audience change tones the notice inside the record that raised it", async () => {
  await administration({
    "/api/admin": () => json(ADMIN_TABLES),
    "/actions/member/": () => json({ applied: false, message: "Only an admin grants access." }),
  });
  await pressRow("Second");

  const record = await screen.findByRole("dialog", { name: "Second" });
  await pick("Web Access Member", "member@example.com");
  await userEvent.click(within(record).getByRole("button", { name: "Grant web access" }));

  await refusedNotice("Only an admin grants access.");
  expect(within(record).getByRole("combobox", { name: "Web Access Member" }).textContent).toBe(
    "member@example.com",
  );
  expect(screen.getByText("<$0.01")).toBeTruthy();
});
