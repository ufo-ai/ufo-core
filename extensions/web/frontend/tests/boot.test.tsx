import { readFileSync } from "node:fs";
import { join } from "node:path";

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { Portal } from "@/Portal";

import { refusedNotice, AGENT, MEMBER, json, useStreamFake, wire } from "./harness";

const STATIC = join(import.meta.dirname, "..", "..", "ufo_ext_web", "static");
const builtPage = () => readFileSync(join(STATIC, "index.html"), "utf8");

const bundle = () => {
  const asset = /src="\/surface\/web\/static\/(assets\/[^"]+\.js)"/.exec(builtPage());
  if (!asset) throw new Error("the built page references no module");
  return readFileSync(join(STATIC, asset[1]), "utf8");
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
  expect(/assets\/index-[A-Za-z0-9_-]+\.js/.test(page)).toBe(true);
  expect(/assets\/index-[A-Za-z0-9_-]+\.css/.test(page)).toBe(true);
  expect(page).toContain("<!doctype html>");
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

  new Function(bundle())();
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
        agents: [AGENT],
        members: [{ id: "m1", email: "member@example.com", admin: true, seated: true }],
        seats: { limit: 5, included: 3 },
        caps: [],
        models: ["opus"],
        reasoning_levels: ["high"],
        deploy: { sandbox_internet: true, extensions: [] },
      }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} subagents={[]} member={{ ...MEMBER, admin: true }} />);

  await userEvent.click(screen.getByRole("button", { name: "Administration" }));
  expect(await screen.findByText("Members · 5 seat limit · 3 included")).toBeTruthy();
  expect(screen.getByText("every member")).toBeTruthy();
  expect(screen.getByText("No spend caps are set.")).toBeTruthy();
  expect(location.hash).toBe("#/admin");
});

test("the admin agent row grants and revokes web access by email", async () => {
  const posted: unknown[] = [];
  const second = { ...AGENT, id: "22222222-2222-4222-8222-222222222222", name: "second", main: false };
  wire({
    "/api/admin": () =>
      json({
        agents: [second],
        members: [],
        seats: { limit: null, included: null },
        caps: [],
        models: ["opus"],
        reasoning_levels: ["high"],
        deploy: { sandbox_internet: false, extensions: [] },
      }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Granted." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[second]} subagents={[]} member={{ ...MEMBER, admin: true }} />);

  await userEvent.click(screen.getByRole("button", { name: "Administration" }));
  await userEvent.type(await screen.findByPlaceholderText("email@work.com"), "new@work.com");
  await userEvent.click(screen.getByRole("button", { name: "Grant" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({ verb: "grant_web_access", email: "new@work.com" });
  expect(screen.getByText("Members · seats ungated")).toBeTruthy();
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
  render(<App agents={[AGENT]} subagents={[]} member={{ ...MEMBER, admin: true }} />);

  await userEvent.click(screen.getByRole("button", { name: "Administration" }));
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
  render(<App agents={[AGENT]} subagents={[]} member={{ ...MEMBER, admin: true }} />);

  const loading = await screen.findByText("Loading…");
  expect(loading.closest("main")).not.toBeNull();
});

test("a refused audience change tones the administration notice", async () => {
  const second = { ...AGENT, id: "22222222-2222-4222-8222-222222222222", name: "second", main: false };
  wire({
    "/api/admin": () =>
      json({
        agents: [second],
        members: [],
        seats: { limit: null, included: null },
        caps: [
          {
            scope: "workspace",
            subject: null,
            window_seconds: 3600,
            limit_micro_usd: 4_000,
            on_breach: "warn",
          },
        ],
        models: ["opus"],
        reasoning_levels: ["high"],
        deploy: { sandbox_internet: false, extensions: [] },
      }),
    "/intents": () => json({ applied: false, message: "Only an admin grants access." }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[second]} subagents={[]} member={{ ...MEMBER, admin: true }} />);

  await userEvent.click(screen.getByRole("button", { name: "Administration" }));
  await userEvent.type(await screen.findByPlaceholderText("email@work.com"), "new@work.com");
  await userEvent.click(screen.getByRole("button", { name: "Grant" }));
  await refusedNotice("Only an admin grants access.");
  expect(screen.getByText("<$0.01")).toBeTruthy();
});
