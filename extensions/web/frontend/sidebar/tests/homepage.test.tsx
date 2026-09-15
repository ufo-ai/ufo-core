import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import type { Agent } from "@/lib/types";

import { AGENT, CHAT_ROW, chatsOnWire, CONVO_ID, json, MEMBER, useStreamFake, wire } from "../../tests/harness";

const HOMEPAGE_URL = "/surface/sites/tok-abc/";

const SET = {
  state: "set",
  url: HOMEPAGE_URL,
  visibility: "workspace",
  updated_at: "2026-08-16T00:00:00Z",
};

const LISTED = {
  id: CONVO_ID,
  agent: null,
  surface: "web",
  surface_label: null,
  audience: "member:m1",
  member_email: MEMBER.email,
  description: "Pick one thread",
  source: null,
  speakers: [MEMBER.email],
  turn_count: 1,
  created_at: "2026-08-01T09:00:00",
  last_turn_at: "2026-08-01T09:00:01",
  readable: true,
  disclosable: false,
  speakable: false,
};

beforeEach(() => {
  location.hash = "";
  useStreamFake();
});

function withHome(homepage: unknown): Agent {
  return { ...AGENT, homepage: homepage as Agent["homepage"] };
}

function open(routes: Parameters<typeof wire>[0], homepage?: unknown) {
  wire({
    "/transcript": () => json({ messages: [] }),
    "/homepage": () => json(homepage ?? { state: "none" }),
    ...routes,
  });
  render(<App agents={[withHome(homepage)]} member={MEMBER} onAgents={() => {}} />);
}

test("a sidebar chat on the chat app opens as a conversation, never inside the app's page", async () => {
  location.hash = "#/";
  wire({
    "/transcript": () => json({ messages: [] }),
    "/homepage": () => json(SET),
    ...chatsOnWire([CHAT_ROW]),
    "/conversations$": () => json({ conversations: [LISTED], more: false }),
  });
  render(
    <App agents={[{ ...withHome(SET), app: "chat" }]} member={MEMBER} onAgents={() => {}} />,
  );

  const rail = within(screen.getByRole("navigation", { name: "Workspace" }));
  fireEvent.click(await rail.findByRole("button", { name: /Pick one thread/ }));

  expect(location.hash).toBe("#/c/" + CONVO_ID);
  expect(await screen.findByLabelText("Ask UFO")).toBeTruthy();
  expect(document.querySelector("iframe")).toBeNull();
});

test("the bare agents hash shows the main agent without navigating", async () => {
  location.hash = "#/agents";
  open({});

  expect(await screen.findByRole("region", { name: "Assistant" })).toBeTruthy();
  expect(location.hash).toBe("#/agents");
});
