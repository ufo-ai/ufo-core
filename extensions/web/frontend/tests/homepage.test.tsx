import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import { AGENT, AGENT_ID, CHAT_ROW, CONVO_ID, MEMBER, json, useStreamFake, wire } from "./harness";

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
};

beforeEach(() => {
  location.hash = "";
  useStreamFake();
});

function open(routes: Parameters<typeof wire>[0]) {
  wire({ "/transcript": () => json({ messages: [] }), ...routes });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
}

test("a set homepage frames the bound site beside the conversation", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  open({
    "/homepage": () => json(SET),
  });

  const frame = await screen.findByTitle("Assistant homepage");
  expect(frame.tagName).toBe("IFRAME");
  expect(frame.getAttribute("src")).toBe(HOMEPAGE_URL);
  expect(frame.hasAttribute("sandbox")).toBe(false);
  // The one act on the half, and the one that leaves the portal.
  const out = screen.getByRole("link", { name: "Open Assistant homepage" });
  expect(out.getAttribute("href")).toBe(HOMEPAGE_URL);
  expect(out.getAttribute("target")).toBe("_blank");
});

/** The conversation stands in a lane of the screen's own track, and a lane is headed by its own
 *  band: the name of what it holds and the way out of it. The conversation draws no band of its own
 *  there, or the name and the way out would each be stated twice. */
test("the conversation beside a page is headed by the lane it stands in", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  open({
    "/homepage": () => json(SET),
  });

  await screen.findByTitle("Assistant homepage");
  await userEvent.click(screen.getByRole("button", { name: "Chat with Assistant" }));

  expect(location.hash).toBe("#/agents/" + AGENT_ID + "?open=new");
  const conversation = await screen.findByRole("region", { name: "Assistant" });
  expect(conversation.querySelector('[data-slot="header"]')).toBeNull();
  const page = screen.getByRole("region", { name: "Assistant homepage" });
  expect(page.querySelector('[data-slot="header"]')).toBeTruthy();
});

/** A page standing alone is the app whole, so the app's conversations stand only once the member
 *  has opened one over it — and they stand between the page and the conversation they open, which
 *  is the order the member walked. */
test("the conversations lane stands with the conversation, between the page and it", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  open({
    "/homepage": () => json(SET),
    "/api/chats": () => json({ chats: [CHAT_ROW] }),
    "/conversations$": () => json({ conversations: [LISTED] }),
  });

  await screen.findByTitle("Assistant homepage");
  expect(screen.queryByRole("region", { name: "Conversations" })).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Chat with Assistant" }));

  const lane = await screen.findByRole("region", { name: "Conversations" });
  const conversation = await screen.findByRole("region", { name: "Assistant" });
  expect(
    lane.compareDocumentPosition(conversation) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
});

test("a homepage being built draws the page's shape rather than the page", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  open({ "/homepage": () => json({ state: "building" }) });

  const half = await screen.findByRole("region", { name: "Assistant homepage" });
  expect(half.getAttribute("aria-busy")).toBe("true");
  expect(half.querySelector("iframe")).toBeNull();
  expect(half.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0);
  // Nothing to open until the build settles.
  expect(screen.queryByRole("link", { name: "Open Assistant homepage" })).toBeNull();
});

test("an app with no homepage draws one column and no second half", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  open({ "/homepage": () => json({ state: "none" }) });

  expect(await screen.findByRole("region", { name: "Assistant" })).toBeTruthy();
  expect(screen.queryByRole("region", { name: "Assistant homepage" })).toBeNull();
  expect(document.querySelector("iframe")).toBeNull();
});

test("the bare agents hash shows the main agent without navigating", async () => {
  location.hash = "#/agents";
  open({});

  expect(await screen.findByRole("region", { name: "Assistant" })).toBeTruthy();
  expect(location.hash).toBe("#/agents");
});
