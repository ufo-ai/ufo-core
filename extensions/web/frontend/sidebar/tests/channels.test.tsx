import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

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

const CHANNELS_HASH = "#/workspace/channels";

function portal(surfaces: SurfaceRow[], surfacesRoute?: () => Response) {
  const wired = wire({
    "/workspace/surfaces$": surfacesRoute ?? (() => json({ surfaces })),
    "/workspace/team$": () => json({ members: [MEMBER], can_add: false, actions: [] }),
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
  location.hash = CHANNELS_HASH;
});

test("the Channels tab draws a row for every channel this deploy offers", async () => {
  portal([SLACK, { ...IMESSAGE, connected: false }, TERMINAL]);

  expect(await screen.findByRole("listitem", { name: "Slack" })).toBeTruthy();
  expect(screen.getByRole("listitem", { name: "iMessage" })).toBeTruthy();
  expect(screen.getByRole("listitem", { name: "Terminal" })).toBeTruthy();
});

test("the workspace tabs name Channels, and the tab opens the rows", async () => {
  location.hash = "#/workspace/team";
  portal([SLACK, IMESSAGE, TERMINAL]);

  await userEvent.click(await screen.findByRole("tab", { name: "Channels" }));
  expect(await screen.findByRole("listitem", { name: "Slack" })).toBeTruthy();
});

test("a refused surfaces read states the refusal once and settles", async () => {
  portal([], () => new Response("nope", { status: 503 }));

  expect(await screen.findByText("Error 503 — reload to retry.")).toBeTruthy();
  await act(async () => {
    await Promise.resolve();
  });
  expect(screen.getAllByText("Error 503 — reload to retry.")).toHaveLength(1);
});
