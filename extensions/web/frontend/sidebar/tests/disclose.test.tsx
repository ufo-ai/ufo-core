import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import {
  heldConversation,
  openConversation,
  refusedNotice,
  AGENT,
  CONVO_ID,
  MEMBER,
  json,
  useStreamFake,
  wire,
} from "./harness";

/** A link naming one of the app's conversations, which is what reaches the gate: the half picks
 *  among none of them, and the address names the one it holds. */
const standing = (id: string) => openConversation(AGENT.id, id);

const ADMIN = { ...MEMBER, admin: true };

const CONVERSATION_ACTIONS = (id: string) => ({
  actions: [
    {
      name: "read_private_transcript",
      description: "",
      input_schema: {},
      call: { kind: "conversation", name: id, action: "read_private_transcript", input: {} },
      label: "Open transcript",
      confirm: "This records that you opened another member's private conversation.",
    },
  ],
});

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
  commentable: false,
};

const WALLED = {
  ...PRIVATE,
  id: "c2",
  audience: "room:slack:C2",
  member_email: null,
  disclosable: false,
};

/** What each of them is called. A conversation the member may not read carries no words of its
 *  own, so one is named by whose it is and one by who may read it. */
const OWNER = "owner@example.com";
const ROOM = "Private channel";

const conversations = (entries: unknown[]) => ({
  "/conversations$": () => json({ conversations: entries }),
});

beforeEach(() => {
  location.hash = "#/agents/" + AGENT.id;
  useStreamFake();
});

/** The gate is offered for a conversation the member can open by acknowledging it and for no other.
 *  One shared with nobody they belong to is not a gate away — the intent would refuse — so the half
 *  states that rather than offering an act that cannot be taken. */
test("a disclosable conversation offers the acknowledgement, one shared with nobody does not", async () => {
  wire(conversations([PRIVATE, WALLED]));
  standing(PRIVATE.id);
  const offered = render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  expect(await screen.findByRole("button", { name: "Open transcript" })).toBeTruthy();
  expect(await heldConversation()).toBe(OWNER);
  offered.unmount();

  standing(WALLED.id);
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  expect(
    await screen.findByText("This conversation is not shared with this account."),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Open transcript" })).toBeNull();
  expect(await heldConversation()).toBe(ROOM);
});

test("the acknowledgement names the owner and what opening records, and does not read yet", async () => {
  const { calls } = wire(conversations([PRIVATE]));
  standing(PRIVATE.id);
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

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
    "/actions/conversation/c1$": () => json(CONVERSATION_ACTIONS("c1")),
    "/actions/conversation/c1/read_private_transcript": (_url, init) => {
      bodies.push(String(init?.body));
      return json({ applied: true, message: "Recorded." });
    },
  });
  standing(PRIVATE.id);
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Open transcript" }));

  await waitFor(() => expect(bodies.length).toBe(1));
  expect(JSON.parse(bodies[0])).toEqual({});
  expect(await screen.findByText("No messages in this conversation yet.")).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Slack · owner@example.com" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Open transcript" })).toBeNull();
});

test("leaving mid-acknowledgement does not open the transcript when the answer lands", async () => {
  let release: ((value: Response) => void) | null = null;
  wire({
    "/transcript": () => json({ messages: [] }),
    ...conversations([PRIVATE, { ...WALLED, disclosable: true }]),
    "/actions/conversation/c1$": () => json(CONVERSATION_ACTIONS("c1")),
    "/actions/conversation/c1/read_private_transcript": () =>
      new Promise<Response>((resolve) => {
        release = resolve;
      }),
  });
  standing(PRIVATE.id);
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Open transcript" }));
  await waitFor(() => expect(release).not.toBeNull());
  standing(WALLED.id);
  await waitFor(async () => expect(await heldConversation()).toBe(ROOM));

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
    "/actions/conversation/": (url, init) => {
      if (init?.method !== "POST") {
        const id = url.split("/actions/conversation/")[1].split("?")[0];
        return json(CONVERSATION_ACTIONS(id));
      }
      if (release) return Response.json({ applied: false, message: "Refused." });
      return new Promise<Response>((resolve) => {
        release = resolve;
      });
    },
  });
  standing(PRIVATE.id);
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Open transcript" }));
  await waitFor(() => expect(release).not.toBeNull());
  standing(WALLED.id);
  await waitFor(async () => expect(await heldConversation()).toBe(ROOM));

  release!(Response.json({ applied: true, message: "Recorded." }));
  await new Promise((resolve) => setTimeout(resolve, 0));

  expect(screen.queryByText("No messages in this conversation yet.")).toBeNull();
  expect(screen.getByRole("button", { name: "Open transcript" })).toBeTruthy();
});

test("a refused acknowledgement states the refusal and opens nothing", async () => {
  const { calls } = wire({
    ...conversations([PRIVATE]),
    "/actions/conversation/c1$": () => json(CONVERSATION_ACTIONS("c1")),
    "/actions/conversation/c1/read_private_transcript": () =>
      json({ applied: false, message: "Only an admin may read it." }),
  });
  standing(PRIVATE.id);
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Open transcript" }));

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
    ["/actions/conversation/" + CONVO_ID + "$"]: () => json(CONVERSATION_ACTIONS(CONVO_ID)),
    ["/actions/conversation/" + CONVO_ID + "/read_private_transcript"]: () =>
      json({ applied: true, message: "Recorded." }),
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

/** The acknowledgement stands under the band naming the conversation it is about, so the member
 *  reads whose it is before taking it and leaves by the acts on that band — and nothing is read
 *  until they do take it. */
test("the band names the conversation the acknowledgement is about, and reads nothing until it is taken", async () => {
  const { calls } = wire(conversations([PRIVATE]));
  standing(PRIVATE.id);
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  expect(await screen.findByRole("button", { name: "Open transcript" })).toBeTruthy();
  expect(await heldConversation()).toBe(OWNER);
  const pane = within(screen.getByRole("region", { name: "Assistant" }));
  expect(pane.getByRole("button", { name: "New" })).toBeTruthy();
  expect(calls.some((url) => url.includes("/transcript"))).toBe(false);
});
