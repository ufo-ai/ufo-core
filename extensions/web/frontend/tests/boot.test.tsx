import { readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { Portal } from "@/Portal";
import { webAudienceLabel } from "@/lib/audience";

import {
  refusedNotice,
  ADMIN_AGENT,
  AGENT,
  MEMBER,
  type Route,
  SECOND_ID,
  json,
  pageFits,
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

async function openAdministration() {
  await userEvent.click(screen.getByRole("button", { name: MEMBER.email }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "Administration" }));
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
  document.body.innerHTML = builtPage().replace(/<script[\s\S]*?<\/script>/g, "");

  await import(pathToFileURL(join(STATIC, entryAsset())).href);
  await waitFor(() => expect(asked).toContain("/surface/web/api/agents"));
  await waitFor(() => expect(document.body.textContent).toContain("Session ended"));
});

test("a session that ends under the open page offers the one sign-in page", async () => {
  wire({ "/api/agents": () => new Response("unauthorized", { status: 401 }) });
  render(<Portal />);

  expect(await screen.findByText("Session ended")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Sign in" }).getAttribute("href")).toBe("/login");
  expect(screen.getByText("ufoctl portal")).toBeTruthy();
  expect(screen.queryByRole("textbox")).toBeNull();
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
  expect(screen.getByRole("link", { name: "Sign in with another email" })).toBeTruthy();
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

test("an admin is offered administration, which reads the admin projection", async () => {
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
  render(<App agents={[AGENT]} subagents={[]} member={{ ...MEMBER, admin: true }} newAgent={null} onAgents={() => {}} />);

  await openAdministration();
  expect(await screen.findByText("Seated")).toBeTruthy();
  expect(screen.getByText("Every member")).toBeTruthy();
  expect(screen.getByText("No spend caps are set.")).toBeTruthy();
  expect(location.hash).toBe("#/admin");
  expect(document.querySelectorAll("h1").length).toBe(1);
});

test("the top bar names the categories on the left and workspace and the member on the right", () => {
  wire({});
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  const bar = screen.getByRole("navigation", { name: "Primary" });
  const names = within(bar)
    .getAllByRole("button")
    .map((entry) => entry.getAttribute("aria-label") ?? entry.textContent);
  expect(names).toEqual(["Chat", "Apps", "Artifacts", "Radar", "Search apps", "Workspace"]);
  const account = screen.getByRole("button", { name: MEMBER.email });
  expect(bar.contains(account)).toBe(false);
  expect(bar.compareDocumentPosition(account) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test("the member's menu states who is signed in and offers the theme choice", async () => {
  wire({});
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await userEvent.click(screen.getByRole("button", { name: MEMBER.email }));

  expect(await screen.findByText(MEMBER.email)).toBeTruthy();
  expect(screen.getByText("Member")).toBeTruthy();
  expect(screen.getByRole("menuitem", { name: /Theme/ })).toBeTruthy();
  expect(screen.queryByRole("menuitem", { name: "Administration" })).toBeNull();
});

const SECOND_AGENT = {
  ...ADMIN_AGENT,
  id: SECOND_ID,
  name: "second",
  main: false,
  installations: ["slack", "ufo"],
};

const ADMIN_TABLES = {
  agents: [SECOND_AGENT],
  members: [{ id: "m1", email: "member@example.com", admin: true, seated: true }],
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

async function administration(routes: Record<string, Route>) {
  wire({ "/transcript": () => json({ messages: [] }), ...routes });
  render(
    <App
      agents={[SECOND_AGENT]}
      subagents={[]}
      member={{ ...MEMBER, admin: true }}
      newAgent={null}
      onAgents={() => {}}
    />,
  );
  await openAdministration();
}

/** Revoking is destructive, so it arms on the first press and commits on the second, as every other
 *  destructive act in the portal does. */
async function revoke(record: HTMLElement): Promise<void> {
  await userEvent.click(within(record).getByRole("button", { name: "Revoke" }));
  await userEvent.click(within(record).getByRole("button", { name: "Confirm revoke" }));
}

/** The tracks are fixed pixels, so a table declaring more of them than the desktop leaves holds its
 *  width and the column scrolls sideways — and the act a row is pressed by is what falls off the
 *  right. Every table administration draws has to fit the page it is drawn on. */
test("every administration table fits the desktop page it is read on", async () => {
  await administration({ "/api/admin": () => json(ADMIN_TABLES) });
  await screen.findByText("second");

  expect(tableFloors()).toEqual([
    "calc(2 * var(--size-fact-column) + 2 * var(--size-prose-column) + 1 * var(--size-act))",
    "calc(2 * var(--size-fact-column) + 2 * var(--size-prose-column) + 0 * var(--size-act))",
    "calc(4 * var(--size-fact-column) + 1 * var(--size-prose-column) + 0 * var(--size-act))",
    "calc(2 * var(--size-fact-column) + 1 * var(--size-prose-column) + 0 * var(--size-act))",
  ]);
  for (const floor of tableFloors()) expect(pageFits(floor)).toBe(true);
});

/** The grant is a field and two acts, which no fixed track holds at the pitch a row is read at. It
 *  stands in the agent's own record instead, with the surfaces the row no longer states. */
test("an agent's record carries its surfaces and the web access grant, and the row does not", async () => {
  const posted: unknown[] = [];
  const lanes: string[] = [];
  const { calls } = wire({
    "/api/admin": () => json({ ...ADMIN_TABLES, members: [], caps: [] }),
    "/intents": (url, init) => {
      lanes.push(url);
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Granted." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  render(
    <App
      agents={[SECOND_AGENT]}
      subagents={[]}
      member={{ ...MEMBER, admin: true }}
      newAgent={null}
      onAgents={() => {}}
    />,
  );

  await openAdministration();
  await screen.findByText("second");
  expect(screen.queryByPlaceholderText("email@work.com")).toBeNull();
  expect(screen.queryByText("Slack, Terminal")).toBeNull();

  await pressRow("second");
  const record = await screen.findByRole("complementary", { name: "second" });
  expect(within(record).getByText("Surfaces")).toBeTruthy();
  expect(within(record).getByText("Slack, Terminal")).toBeTruthy();

  await userEvent.type(within(record).getByLabelText("Web Access Address"), "new@work.com");
  await userEvent.click(within(record).getByRole("button", { name: "Grant" }));
  await waitFor(() => expect(posted.length).toBe(1));

  await revoke(record);
  await waitFor(() => expect(posted.length).toBe(2));

  expect(posted).toEqual([
    { verb: "grant_web_access", email: "new@work.com" },
    { verb: "revoke_web_access", email: "new@work.com" },
  ]);
  expect(lanes).toEqual([
    "/surface/web/agents/" + SECOND_ID + "/intents",
    "/surface/web/agents/" + SECOND_ID + "/intents",
  ]);
  expect(calls.filter((url) => url.includes("/api/admin")).length).toBe(3);
});

const GRANT_ROUTES = {
  "/api/admin": () => json({ ...ADMIN_TABLES, members: [], caps: [] }),
};

async function grantForm(): Promise<HTMLElement> {
  await pressRow("second");
  return await screen.findByRole("complementary", { name: "second" });
}

/** One field, two acts, one contract. The address is declared by the field, so the field is what
 *  refuses it — and it refuses whichever act was pressed, or the act the browser does not gate is a
 *  way around the one it does. */
test("neither web access act posts an address the field refuses", async () => {
  const posted: unknown[] = [];
  await administration({
    ...GRANT_ROUTES,
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Granted." });
    },
  });
  const record = await grantForm();

  await userEvent.type(within(record).getByLabelText("Web Access Address"), "nonsense");
  await userEvent.click(within(record).getByRole("button", { name: "Grant" }));
  await revoke(record);

  expect(posted).toEqual([]);
});

/** A press on a live control always lands somewhere the member can read. An empty box is the
 *  field's own refusal, raised on the field, rather than a handler returning in silence. */
test("an empty web access box answers the press on the field", async () => {
  const posted: unknown[] = [];
  await administration({
    ...GRANT_ROUTES,
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Revoked." });
    },
  });
  const record = await grantForm();
  const field = within(record).getByLabelText<HTMLInputElement>("Web Access Address");
  const refused: boolean[] = [];
  field.addEventListener("invalid", () => refused.push(field.validity.valueMissing));

  await revoke(record);

  expect(posted).toEqual([]);
  expect(refused).toEqual([true]);
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
  await administration({
    "/api/admin": () =>
      json({ ...ADMIN_TABLES, agents: [SECOND_AGENT, GRANTED_AGENT], members: [], caps: [] }),
  });
  await screen.findByText("second");

  expect(screen.getByText(webAudienceLabel(false, []))).toBeTruthy();
  expect(screen.getByText(webAudienceLabel(false, GRANTED_AGENT.web_audience))).toBeTruthy();

  const record = await grantForm();
  expect(within(record).getByText(webAudienceLabel(false, []))).toBeTruthy();
});

/** The main agent answers every member, so its record offers no address to grant. */
test("the main agent's record states its audience and carries no grant form", async () => {
  await administration({
    "/api/admin": () => json({ ...ADMIN_TABLES, agents: [ADMIN_AGENT] }),
  });
  await pressRow("assistant");

  const record = await screen.findByRole("complementary", { name: "assistant" });
  expect(within(record).getByText("Every member")).toBeTruthy();
  expect(within(record).queryByRole("button", { name: "Grant" })).toBeNull();
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
  render(<App agents={[AGENT]} subagents={[]} member={{ ...MEMBER, admin: true }} newAgent={null} onAgents={() => {}} />);

  await openAdministration();
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
  render(<App agents={[AGENT]} subagents={[]} member={{ ...MEMBER, admin: true }} newAgent={null} onAgents={() => {}} />);

  const loading = await screen.findByTestId("admin-loading");
  expect(loading.closest("main")).not.toBeNull();
});

/** A refusal has no field to answer it, so it stands in the record the act was raised in — with
 *  what the member typed still in the box — and the page behind it keeps reading. */
test("a refused audience change tones the notice inside the record that raised it", async () => {
  await administration({
    "/api/admin": () => json({ ...ADMIN_TABLES, members: [] }),
    "/intents": () => json({ applied: false, message: "Only an admin grants access." }),
  });
  await pressRow("second");

  const record = await screen.findByRole("complementary", { name: "second" });
  await userEvent.type(within(record).getByLabelText("Web Access Address"), "new@work.com");
  await userEvent.click(within(record).getByRole("button", { name: "Grant" }));

  await refusedNotice("Only an admin grants access.");
  expect(within(record).getByLabelText<HTMLInputElement>("Web Access Address").value).toBe(
    "new@work.com",
  );
  expect(screen.getByText("<$0.01")).toBeTruthy();
});
