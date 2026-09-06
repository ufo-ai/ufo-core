import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import type { SurfaceRow } from "@/views/Surfaces";

import { AGENT, json, MEMBER, useStreamFake, wire } from "./harness";

const SLACK: SurfaceRow = {
  name: "slack",
  label: "Slack",
  offered: true,
  connected: true,
  install_command: null,
};
const IMESSAGE: SurfaceRow = {
  name: "imessage",
  label: "iMessage",
  offered: true,
  connected: true,
  install_command: null,
};
const TERMINAL: SurfaceRow = {
  name: "ufo",
  label: "Terminal",
  offered: true,
  connected: true,
  install_command: "curl ufo.example.com | sh",
};

function portal(surfaces: SurfaceRow[], surfacesRoute?: () => Response) {
  const wired = wire({
    "/workspace/surfaces$": surfacesRoute ?? (() => json({ surfaces })),
    "/workspace/first-run$": () =>
      json({
        providers: [],
        connectors: [{ name: "slack", label: "Slack", installed: false }],
        actions: { member: [], memory: [], enrichment_profile: [] },
        model_key_held: false,
      }),
    "/actions/surface/imessage$": () => json({ actions: [] }),
    "/transcript": () => json({ messages: [] }),
    "/objects/": () => json({ objects: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  return wired;
}

beforeEach(() => {
  useStreamFake();
  location.hash = "";
});

/** The member's pill at the foot of the sidebar: every act on their own session stands behind it,
 *  Channels among them. */
async function pill() {
  const rail = await screen.findByRole("navigation", { name: "Workspace" });
  return within(rail).getByRole("button", { name: MEMBER.email });
}

/** Opens the Channels dialog the way a member does: the pill, then the item under it. */
async function openChannels() {
  await userEvent.click(await pill());
  await userEvent.click(await screen.findByRole("menuitem", { name: "Channels" }));
}

test("the member's pill carries the Channels act", async () => {
  portal([SLACK, IMESSAGE, TERMINAL]);

  await userEvent.click(await pill());
  expect(await screen.findByRole("menuitem", { name: "Channels" })).toBeTruthy();
});

test("an unconnected channel marks the pill, and three connected ones do not", async () => {
  portal([{ ...SLACK, connected: false }, IMESSAGE, TERMINAL]);

  const marked = await pill();
  await expect
    .poll(() => marked.querySelector(".bg-attention-ink"))
    .not.toBeNull();
});

test("every channel connected leaves the pill unmarked", async () => {
  portal([SLACK, IMESSAGE, TERMINAL]);

  const button = await pill();
  await expect.poll(() => button.querySelector(".bg-attention-ink")).toBeNull();
});

test("the act opens the Channels dialog on the three rows", async () => {
  portal([SLACK, { ...IMESSAGE, connected: false }, TERMINAL]);

  await openChannels();

  const dialog = await screen.findByRole("dialog", { name: "Channels" });
  expect(await within(dialog).findByRole("listitem", { name: "Slack" })).toBeTruthy();
  expect(within(dialog).getByRole("listitem", { name: "iMessage" })).toBeTruthy();
  expect(within(dialog).getByRole("listitem", { name: "Terminal" })).toBeTruthy();
});

test("a channel this deploy does not offer leaves the pill unmarked", async () => {
  portal([{ ...SLACK, offered: false, connected: false }, IMESSAGE, TERMINAL]);

  const button = await pill();
  await expect.poll(() => button.querySelector(".bg-attention-ink")).toBeNull();
});

test("the dot does not put the surfaces read on a three-second loop", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const { calls } = portal([{ ...SLACK, connected: false }, IMESSAGE, TERMINAL]);

  const marked = await pill();
  const reads = () => calls.filter((url) => url.includes("/workspace/surfaces")).length;
  await expect.poll(() => marked.querySelector(".bg-attention-ink")).not.toBeNull();
  expect(reads()).toBe(1);

  await act(async () => {
    vi.advanceTimersByTime(10_000);
  });
  expect(reads()).toBe(1);
  vi.useRealTimers();
});

test("a refused surfaces read states the refusal once and settles", async () => {
  portal([], () => new Response("nope", { status: 503 }));

  await openChannels();

  expect(await screen.findByText("Error 503 — reload to retry.")).toBeTruthy();
  await act(async () => {
    await Promise.resolve();
  });
  expect(screen.getAllByText("Error 503 — reload to retry.")).toHaveLength(1);
});
