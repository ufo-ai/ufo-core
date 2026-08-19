import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import {
  refusedNotice,
  AGENT,
  CONVO_ID,
  FRESH,
  MEMBER,
  json,
  pickConversation,
  useStreamFake,
  wire,
} from "./harness";

const ADMIN = { ...MEMBER, admin: true };

const PRIVATE = {
  id: "c1",
  surface: "slack",
  surface_label: null,
  audience: "member:0a1b2c3d-0000-4000-8000-000000000009",
  member_email: "owner@example.com",
  description: "",
  speakers: [],
  turn_count: 3,
  created_at: "2026-07-30T10:00:00",
  last_turn_at: "2026-07-30T11:00:00",
  readable: false,
  disclosable: true,
};

const WALLED = {
  ...PRIVATE,
  id: "c2",
  audience: "room:slack:C2",
  member_email: null,
  disclosable: false,
};

/** What the switcher calls each of them. A conversation the member may not read carries no words of
 *  its own, so one is named by whose it is and one by who may read it. */
const OWNER = "owner@example.com";
const ROOM = "Private channel";

/** What the half is called before a conversation is picked, which is where the switcher opens
 *  from. */
const conversations = (entries: unknown[]) => ({
  "/conversations$": () => json({ conversations: entries }),
});

beforeEach(() => {
  location.hash = "#/agents/" + AGENT.id;
  useStreamFake();
});

test("a disclosable conversation is offered, one shared with nobody is not", async () => {
  wire(conversations([PRIVATE, WALLED]));
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: FRESH }));

  expect(await screen.findByRole("menuitemradio", { name: OWNER })).toBeTruthy();
  expect(screen.queryByRole("menuitemradio", { name: ROOM })).toBeNull();
});

test("the acknowledgement names the owner and what opening records, and does not read yet", async () => {
  const { calls } = wire(conversations([PRIVATE]));
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await pickConversation(FRESH, OWNER);

  const warning = await screen.findByText(/private to owner@example.com/);
  expect(warning.textContent).toContain("may contain private information");
  expect(warning.textContent).toContain("records your email, theirs, and the time");
  expect(warning.textContent).not.toContain("can read that record");
  expect(calls.some((url) => url.includes("/transcript"))).toBe(false);
});

test("acknowledging posts the transcript intent and opens the conversation it named", async () => {
  const bodies: string[] = [];
  wire({
    "/transcript": () => json({ messages: [] }),
    ...conversations([PRIVATE]),
    "/intents": (_url, init) => {
      bodies.push(String(init?.body));
      return json({ applied: true, message: "Recorded." });
    },
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await pickConversation(FRESH, OWNER);
  await userEvent.click(screen.getByRole("button", { name: "Open transcript" }));

  await waitFor(() => expect(bodies.length).toBe(1));
  expect(JSON.parse(bodies[0])).toEqual({
    verb: "read",
    kind: "transcript",
    conversation_id: "c1",
  });
  expect(await screen.findByText("No messages in this conversation yet.")).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Slack · owner@example.com" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Open transcript" })).toBeNull();
});

test("leaving mid-acknowledgement does not open the transcript when the answer lands", async () => {
  let release: ((value: Response) => void) | null = null;
  wire({
    "/transcript": () => json({ messages: [] }),
    ...conversations([PRIVATE, { ...WALLED, disclosable: true }]),
    "/intents": () =>
      new Promise<Response>((resolve) => {
        release = resolve;
      }),
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await pickConversation(FRESH, OWNER);
  await userEvent.click(screen.getByRole("button", { name: "Open transcript" }));
  await waitFor(() => expect(release).not.toBeNull());
  await pickConversation(OWNER, ROOM);

  release!(Response.json({ applied: true, message: "Recorded." }));
  await new Promise((resolve) => setTimeout(resolve, 0));

  expect(screen.queryByText("No messages in this conversation yet.")).toBeNull();
  expect(screen.getByRole("button", { name: "Open transcript" })).toBeTruthy();
});

test("an acknowledgement in flight for one conversation never opens over another", async () => {
  let release: ((value: Response) => void) | null = null;
  wire({
    "/transcript": () => json({ messages: [] }),
    ...conversations([PRIVATE, { ...WALLED, disclosable: true }]),
    "/intents": () => {
      if (release) return Response.json({ applied: false, message: "Refused." });
      return new Promise<Response>((resolve) => {
        release = resolve;
      });
    },
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await pickConversation(FRESH, OWNER);
  await userEvent.click(screen.getByRole("button", { name: "Open transcript" }));
  await waitFor(() => expect(release).not.toBeNull());
  await pickConversation(OWNER, ROOM);

  release!(Response.json({ applied: true, message: "Recorded." }));
  await new Promise((resolve) => setTimeout(resolve, 0));

  expect(screen.queryByText("No messages in this conversation yet.")).toBeNull();
  expect(screen.getByRole("button", { name: "Open transcript" })).toBeTruthy();
});

test("a refused acknowledgement states the refusal and opens nothing", async () => {
  const { calls } = wire({
    ...conversations([PRIVATE]),
    "/intents": () => json({ applied: false, message: "Only an admin may read it." }),
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await pickConversation(FRESH, OWNER);
  await userEvent.click(screen.getByRole("button", { name: "Open transcript" }));

  await refusedNotice("Only an admin may read it.");
  expect(calls.some((url) => url.includes("/transcript"))).toBe(false);
});

test("a permalink to another member's conversation offers the listing's acknowledgement", async () => {
  location.hash = "#/c/" + CONVO_ID;
  const linked = { ...PRIVATE, id: CONVO_ID, agent: { id: AGENT.id, name: AGENT.name } };
  const { calls } = wire({
    "/api/chats": (url) =>
      url.includes("conversation=")
        ? json({ chats: [], conversation: linked })
        : json({ chats: [] }),
    "/transcript": () => json({ messages: [] }),
    "/intents": () => json({ applied: true, message: "Recorded." }),
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  expect(await screen.findByText(/private to owner@example.com/)).toBeTruthy();
  expect(calls.some((url) => url.includes("/transcript"))).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "Open transcript" }));

  expect(await screen.findByText("No messages in this conversation yet.")).toBeTruthy();
  expect(
    screen.getByText("This conversation is read-only here. Reply in Slack to continue it."),
  ).toBeTruthy();
});

test("a permalink to a conversation naming no member is unshared, not missing", async () => {
  location.hash = "#/c/" + CONVO_ID;
  const linked = { ...WALLED, id: CONVO_ID, agent: { id: AGENT.id, name: AGENT.name }, member_email: null };
  wire({
    "/api/chats": (url) =>
      url.includes("conversation=")
        ? json({ chats: [], conversation: linked })
        : json({ chats: [] }),
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  expect(
    await screen.findByText("This conversation is not shared with this account."),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Open transcript" })).toBeNull();
});

/** A read that failed is not an app nobody has spoken to: the half states the error, or the member
 *  is handed the composer over a history that is there and never learns the projection refused. */
test("a failed conversations read states the error rather than the composer", async () => {
  wire({ "/conversations$": () => new Response("nope", { status: 503 }) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  expect(await screen.findByText("Error 503 — reload to retry.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
});

/** The acknowledgement stands under the switcher that raised it rather than over it, so the
 *  conversation it is about is still there to leave by — and nothing is read until the member takes
 *  it. */
test("the switcher stands over the acknowledgement, which reads nothing until it is taken", async () => {
  const { calls } = wire(conversations([PRIVATE]));
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await pickConversation(FRESH, OWNER);

  expect(await screen.findByRole("button", { name: "Open transcript" })).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: OWNER }));
  expect(await screen.findByRole("menuitemradio", { name: OWNER })).toBeTruthy();
  expect(calls.some((url) => url.includes("/transcript"))).toBe(false);
});
