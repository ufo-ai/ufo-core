import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { Portal } from "@/Portal";
import { chatHash, type Route } from "@/lib/route";
import { pageTitle } from "@/lib/title";
import type { OwnedConversation } from "@/lib/types";

import {
  AGENT,
  AGENT_ID,
  CHAT_ROW,
  CONVO_ID,
  MEMBER,
  SECOND,
  SECOND_ID,
  json,
  useStreamFake,
  wire,
} from "./harness";

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
};

const titled = (route: Route, linked: Record<string, OwnedConversation> = {}) =>
  pageTitle(route, [AGENT, SECOND], [CHAT_ROW], linked, AGENT);

beforeEach(() => {
  useStreamFake();
});

test("every page names where the member is, innermost first, then the product", () => {
  expect(titled({ kind: "home" })).toBe("New chat · assistant · ufo");
  expect(titled({ kind: "new-chat", agentId: SECOND_ID })).toBe("New chat · second · ufo");
  expect(titled({ kind: "agents" })).toBe("Apps · ufo");
  expect(titled({ kind: "agent", agentId: AGENT_ID, tab: "home", ...PLACE })).toBe(
    "Home · assistant · ufo",
  );
  expect(titled({ kind: "agent", agentId: AGENT_ID, tab: "conversations", ...PLACE })).toBe(
    "Conversations · assistant · ufo",
  );
  expect(titled({ kind: "workspace", view: "team", ...PLACE })).toBe("Team · Workspace · ufo");
  expect(titled({ kind: "workspace", view: "credentials", ...PLACE })).toBe(
    "Credentials · Workspace · ufo",
  );
  expect(titled({ kind: "section", section: "radar", ...PLACE })).toBe("Radar · ufo");
  expect(titled({ kind: "section", section: "artifacts", ...PLACE })).toBe("Artifacts · ufo");
  expect(titled({ kind: "admin" })).toBe("Administration · ufo");
  expect(titled({ kind: "bad-link" })).toBe("Invalid link · ufo");
  expect(
    titled({
      kind: "conversation-slot",
      agentId: AGENT_ID,
      conversationId: CONVO_ID,
      slot: "changes",
    }),
  ).toBe("changes · assistant · ufo");
});

test("a conversation is named by its own subject, and one still unread by the product alone", () => {
  expect(titled({ kind: "chat", conversationId: CONVO_ID })).toBe(
    "Pick one thread · assistant · ufo",
  );
  expect(titled({ kind: "chat", conversationId: "unknown" })).toBe("ufo");
  expect(titled({ kind: "chat", conversationId: "linked" }, { linked: LINKED })).toBe(
    "Ship the release · assistant · ufo",
  );
});

test("the tab follows the hash the member opens", async () => {
  location.hash = chatHash(CONVO_ID);
  wire({
    "/api/chats": () => json({ chats: [CHAT_ROW] }),
    "/transcript": () => json({ messages: [] }),
    "/slots": () => json({ slots: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByText("No messages in this conversation yet.")).toBeTruthy();
  await waitFor(() => expect(document.title).toBe("Pick one thread · assistant · ufo"));

  location.hash = "#/agents";
  await waitFor(() => expect(document.title).toBe("Apps · ufo"));
});

test("a tab whose session ended says so", async () => {
  wire({ "/api/agents": () => new Response("unauthorized", { status: 401 }) });
  render(<Portal />);

  expect(await screen.findByText("Session ended")).toBeTruthy();
  await waitFor(() => expect(document.title).toBe("Session ended · ufo"));
});
