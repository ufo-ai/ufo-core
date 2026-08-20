import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";

import { AGENT, AGENT_ID, MEMBER, StreamFake, TURN_ID, json, useStreamFake, wire } from "./harness";

const SLACK_LINK = "https://slack.com/oauth/v2/authorize?state=sealed";

const CATALOG = {
  providers: [
    { name: "slack", label: "Slack" },
    { name: "github", label: "GitHub" },
    { name: "notion", label: "Notion" },
    { name: "gmail", label: "Gmail" },
  ],
  connectors: [
    { name: "slack", label: "Slack", installed: false },
    { name: "github", label: "GitHub", installed: true },
  ],
};

const POOLED_NOTION = {
  connections: [
    {
      provider: "notion",
      account_id: "acct-1",
      account_label: "Notion team",
      owner_email: "member@example.com",
      own: true,
      shared: false,
      connected_at: "2026-08-01T09:00:00",
      grant: "g1",
      agents: [],
    },
  ],
};

beforeEach(() => {
  document.body.innerHTML = "";
  location.hash = "#/connect";
  useStreamFake();
});

function recorder(outcome: unknown): { calls: { url: string; body: unknown }[] } {
  const calls: { url: string; body: unknown }[] = [];
  wire({
    "/workspace/first-run": () => json(CATALOG),
    "/connections": () => json({ connections: [] }),
    "/intents": (url, init) => {
      calls.push({ url, body: JSON.parse(String(init?.body)) });
      return json(outcome);
    },
  });
  return { calls };
}

/** The member coming back from the provider's pages: the tab they left is looked at again, which
 *  re-reads what the tile states. */
async function returning() {
  for (const state of ["hidden", "visible"]) {
    Object.defineProperty(document, "visibilityState", { value: state, configurable: true });
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
  }
}

test("the page offers every catalog tool and marks the connected ones", async () => {
  wire({
    "/workspace/first-run": () => json(CATALOG),
    "/connections": () => json(POOLED_NOTION),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByRole("button", { name: "Slack" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Gmail" })).toBeTruthy();
  expect(screen.getByLabelText("GitHub connected")).toBeTruthy();
  expect(screen.getByLabelText("Notion connected")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "GitHub" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Notion" })).toBeNull();
  expect(screen.getByRole("link", { name: "Manage connected accounts" })).toBeTruthy();
});

test("a tile draws its provider's own mark, never the plug", async () => {
  wire({
    "/workspace/first-run": () =>
      json({
        providers: [
          { name: "googlesheets", label: "Google Sheets" },
          { name: "attio", label: "Attio" },
          { name: "discord", label: "Discord" },
        ],
        connectors: [],
      }),
    "/connections": () => json({ connections: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const vendored = (tile: HTMLElement) =>
    tile.querySelector("[style*=background-image]")?.getAttribute("style") ?? "";
  const sheets = await screen.findByRole("button", { name: "Google Sheets" });
  expect(vendored(sheets)).toContain("--brand-googlesheets");
  const discord = screen.getByRole("button", { name: "Discord" });
  expect(vendored(discord)).toContain("--brand-discord");
  const attio = screen.getByRole("button", { name: "Attio" });
  expect(vendored(attio)).toContain("--brand-attio");
});

test("Connect stands current in the bar on its own page", async () => {
  wire({
    "/workspace/first-run": () => json(CATALOG),
    "/connections": () => json({ connections: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByRole("heading", { name: "Connect" })).toBeTruthy();
  const bar = screen
    .getAllByRole("button", { name: "Connect" })
    .find((button) => button.getAttribute("aria-current") === "true");
  expect(bar).toBeTruthy();
});

test("a tile connects the member's account through the main agent, in a window this page owns", async () => {
  const { calls } = recorder({ applied: true, message: "", turn_id: TURN_ID });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  const consent = { focus: vi.fn(), close: vi.fn(), location: { href: "" } };
  const opened = vi.spyOn(window, "open").mockReturnValue(consent as unknown as Window);

  await userEvent.click(await screen.findByRole("button", { name: "Notion" }));

  expect(calls[0].url).toContain("/agents/" + AGENT_ID + "/intents");
  expect(calls[0].body).toEqual({
    verb: "connect",
    kind: "connection",
    name: "notion",
    spec: { shared: false },
  });
  expect(opened.mock.calls[0][1]).toBe("ufo-connect");

  StreamFake.last().emit("connect", { provider: "notion", label: "Notion", turn: TURN_ID });

  await waitFor(() => expect(consent.location.href).toBe("/surface/web/turns/" + TURN_ID + "/connect"));
  expect(screen.queryByRole("link", { name: "Open the provider consent page" })).toBeNull();
  opened.mockRestore();
});

test("a consent the browser refuses still renders the link", async () => {
  recorder({ applied: true, message: "", turn_id: TURN_ID });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Notion" }));
  StreamFake.last().emit("connect", { provider: "notion", label: "Notion", turn: TURN_ID });

  const link = await screen.findByRole("link", { name: "Open the provider consent page" });
  expect(link.getAttribute("href")).toBe("/surface/web/turns/" + TURN_ID + "/connect");
});

test("a workspace install dispatches its own verb and hands back its install page", async () => {
  const { calls } = recorder({ applied: true, message: "", url: SLACK_LINK });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Slack" }));

  expect(calls[0].body).toEqual({ verb: "connect_slack" });
  const link = await screen.findByRole("link", { name: "Open the Slack install page" });
  expect(link.getAttribute("href")).toBe(SLACK_LINK);
});

test("a refused intent states itself and the tile stays open", async () => {
  recorder({ applied: false, message: "Only a workspace admin connects Slack." });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Slack" }));

  expect(await screen.findByText("Only a workspace admin connects Slack.")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Slack" })).toBeTruthy();
});

test("the tile fills when the account lands", async () => {
  let pool: unknown = { connections: [] };
  wire({
    "/workspace/first-run": () => json(CATALOG),
    "/connections": () => json(pool),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  expect(await screen.findByRole("button", { name: "Notion" })).toBeTruthy();

  pool = POOLED_NOTION;
  await returning();

  expect(await screen.findByLabelText("Notion connected")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Notion" })).toBeNull();
});
