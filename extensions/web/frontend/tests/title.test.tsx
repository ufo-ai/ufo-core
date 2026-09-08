import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { Portal } from "@/Portal";
import { agentHash, chatHash, type Route } from "@/lib/route";
import { pageCrumb, pageTitle } from "@/lib/title";
import type { OwnedConversation } from "@/lib/types";

import { AGENT, AGENT_ID, CHAT_ROW, chatsOnWire, CONVO_ID, json, MEMBER, SECOND, SECOND_ID, useStreamFake, wire } from "./harness";

const PLACE = { place: {} };

const LINKED: OwnedConversation = {
  id: CONVO_ID,
  agent: { id: AGENT_ID, name: "assistant" },
  surface: "slack",
  surface_label: null,
  audience: "room:slack:C2",
  member_email: "owner@example.com",
  description: "Ship the release",
  source: null,
  speakers: [],
  turn_count: 3,
  created_at: "2026-07-30T10:00:00",
  last_turn_at: "2026-07-30T11:00:00",
  readable: true,
  disclosable: false,
  commentable: false,
};

const titled = (route: Route, linked: Record<string, OwnedConversation> = {}) =>
  pageTitle(route, [AGENT, SECOND], [CHAT_ROW], linked);

const crumbed = (route: Route, linked: Record<string, OwnedConversation> = {}) =>
  pageCrumb(route, [AGENT, SECOND], [CHAT_ROW], linked);

beforeEach(() => {
  useStreamFake();
});

test("every page names where the member is, innermost first, then the product", () => {
  expect(titled({ kind: "home", ...PLACE })).toBe("Home · ufo");
  expect(titled({ kind: "new-chat", agentId: SECOND_ID })).toBe("New conversation · Second · ufo");
  expect(titled({ kind: "builder" })).toBe("App Creator · Apps · Workspace · ufo");
  expect(titled({ kind: "agent", agentId: AGENT_ID, ...PLACE })).toBe("Assistant · ufo");
  expect(titled({ kind: "workspace", view: "team", ...PLACE })).toBe("Team · Workspace · ufo");
  expect(titled({ kind: "workspace", view: "credentials", ...PLACE })).toBe(
    "Credentials · Workspace · ufo",
  );
  expect(titled({ kind: "section", section: "connectors", ...PLACE })).toBe("Connectors · ufo");
  expect(titled({ kind: "first-run" })).toBe("Set up this workspace · ufo");
  expect(titled({ kind: "bad-link" })).toBe("Invalid link · ufo");
  expect(
    titled({
      kind: "conversation-slot",
      agentId: AGENT_ID,
      conversationId: CONVO_ID,
      slot: "changes",
    }),
  ).toBe("changes · Assistant · ufo");
});

test("a conversation is named by its own subject, and one still unread by the product alone", () => {
  expect(titled({ kind: "chat", conversationId: CONVO_ID })).toBe(
    "Pick one thread · Assistant · ufo",
  );
  expect(titled({ kind: "chat", conversationId: "unknown" })).toBe("ufo");
  expect(titled({ kind: "chat", conversationId: "linked" }, { linked: LINKED })).toBe(
    "Ship the release · Assistant · ufo",
  );
});

test("the crumb is the step the tab title names after the page", () => {
  expect(crumbed({ kind: "chat", conversationId: CONVO_ID })).toEqual({
    label: "Assistant",
    at: agentHash(AGENT_ID),
  });
  expect(crumbed({ kind: "chat", conversationId: "linked" }, { linked: LINKED })).toEqual({
    label: "Assistant",
    at: agentHash(AGENT_ID),
  });
  expect(crumbed({ kind: "workspace", view: "team", ...PLACE })).toEqual({ label: "Workspace" });
  expect(crumbed({ kind: "builder" })).toEqual({ label: "Apps", at: "#/agents" });
  expect(crumbed({ kind: "agent", agentId: AGENT_ID, ...PLACE })).toBeUndefined();
  expect(crumbed({ kind: "home", ...PLACE })).toBeUndefined();
});

test("home is named for itself, whatever its track holds", () => {
  expect(titled({ kind: "home", place: { opens: [AGENT_ID, SECOND_ID] } })).toBe("Home · ufo");
  expect(crumbed({ kind: "home", place: { opens: [AGENT_ID] } })).toBeUndefined();
});

test("a step no roster row reaches is the app's name and nothing to press", () => {
  const held = { ...CHAT_ROW, agent_id: SECOND_ID, agent_name: "daily-brief" };
  const route: Route = { kind: "chat", conversationId: CONVO_ID };
  expect(pageCrumb(route, [AGENT], [held], {})).toEqual({ label: "Daily-Brief" });
});

test("the tab follows the hash the member opens", async () => {
  location.hash = chatHash(CONVO_ID);
  wire({
    ...chatsOnWire([CHAT_ROW]),
    "/transcript": () => json({ messages: [] }),
    "/slots": () => json({ slots: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("No messages in this conversation yet.")).toBeTruthy();
  await waitFor(() => expect(document.title).toBe("Pick one thread · Assistant · ufo"));

  location.hash = "#/agents";
  await waitFor(() => expect(document.title).toBe("Apps · Workspace · ufo"));
});

test("a tab whose email holds no member row says so", async () => {
  wire({
    "/api/agents": () =>
      new Response("no member with this email in this workspace", {
        status: 401,
        headers: { "x-ufo-session-fault": "no-member" },
      }),
  });
  render(<Portal />);

  expect(await screen.findByText("Not a member of this workspace")).toBeTruthy();
  await waitFor(() => expect(document.title).toBe("Not a member of this workspace · ufo"));
});
