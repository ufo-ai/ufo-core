import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import type { Agent } from "@/lib/types";

import type { Conversation } from "@/lib/types";

import { AGENT, CHAT_ROW, chatsOnWire, conversationDetail, conversationObject, CONVO_ID, heldConversation, json, MEMBER, openConversation, refusedNotice, useStreamFake, wire } from "./harness";

const standing = (id: string) => openConversation(AGENT.id, id);

const ADMIN = { ...MEMBER, admin: true };

const HOSTING = {
  ...AGENT,
  homepage: { state: "set", url: "https://app.example.test" } as Agent["homepage"],
};

const PRIVATE: Conversation = {
  ...CHAT_ROW,
  conversation_id: "c1",
  surface: "slack",
  surface_label: null,
  audience: "member:0a1b2c3d-0000-4000-8000-000000000009",
  member_email: "owner@example.com",
  title: "",
  last_at: "2026-07-30T11:00:00",
  readable: false,
  disclosable: true,
  speakable: false,
};

const WALLED: Conversation = {
  ...PRIVATE,
  conversation_id: "c2",
  audience: "room:slack:C2",
  member_email: null,
  disclosable: false,
};

const OWNER = "owner@example.com";
const ROOM = "Private channel";

const OPEN_TRANSCRIPT = (id: string) => ({
  name: "read_private_transcript",
  description: "Record that an admin opened another member's private conversation.",
  input_schema: { properties: {} },
  call: { kind: "conversation", action: "read_private_transcript", name: id, input: {} },
  label: "Open transcript",
});

const conversations = (entries: Conversation[]) => ({
  ...chatsOnWire(entries),
  ...Object.fromEntries(
    entries.map((entry) => [
      "/actions/conversation/" + entry.conversation_id + "$",
      () => json({ actions: [OPEN_TRANSCRIPT(entry.conversation_id)] }),
    ]),
  ),
});

beforeEach(() => {
  location.hash = "#/agents/" + AGENT.id;
  useStreamFake();
});

test("a disclosable conversation offers the acknowledgement, one shared with nobody does not", async () => {
  wire(conversations([PRIVATE, WALLED]));
  standing(PRIVATE.conversation_id);
  const offered = render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  expect(await screen.findByRole("button", { name: "Open transcript" })).toBeTruthy();
  expect(await heldConversation()).toBe(OWNER);
  offered.unmount();

  standing(WALLED.conversation_id);
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  expect(
    await screen.findByText("This conversation is not shared with this account."),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Open transcript" })).toBeNull();
});

test("the acknowledgement names the owner and what opening records, and does not read yet", async () => {
  const { calls } = wire(conversations([PRIVATE]));
  standing(PRIVATE.conversation_id);
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  const warning = await screen.findByText(/private to owner@example.com/);
  expect(warning.textContent).toContain("may contain private information");
  expect(warning.textContent).toContain("records your email, theirs, and the time");
  expect(warning.textContent).not.toContain("can read that record");
  expect(calls.some((url) => url.includes("/transcript"))).toBe(false);
});

test("acknowledging posts the transcript intent and opens the conversation it named", async () => {
  const posted: { url: string; body: unknown }[] = [];
  wire({
    "/transcript": () => json({ messages: [] }),
    "/read_private_transcript": (url, init) => {
      posted.push({ url, body: JSON.parse(String(init?.body)) });
      return json({ applied: true, message: "Recorded." });
    },
    ...conversations([PRIVATE]),
  });
  standing(PRIVATE.conversation_id);
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Open transcript" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toEqual({
    url: "/surface/web/agents/" + AGENT.id + "/actions/conversation/c1/read_private_transcript",
    body: {},
  });
  expect(await screen.findByText("No messages in this conversation yet.")).toBeTruthy();
  expect(await heldConversation()).toBe(OWNER);
  expect(screen.queryByLabelText("Ask UFO")).toBeNull();
  expect(screen.queryByRole("button", { name: "Open transcript" })).toBeNull();
});

test("a disclosure taken on the agent screen holds when a failed poll unmounts the pane", async () => {
  let failing = false;
  const posted: string[] = [];
  wire({
    "/transcript": () => json({ messages: [] }),
    "/read_private_transcript": (url) => {
      posted.push(url);
      return json({ applied: true, message: "Recorded." });
    },
    ...conversations([PRIVATE]),
    "/objects/conversation$": (url) => {
      if (url.includes("readable=false")) {
        return json({ objects: [conversationObject(PRIVATE)] });
      }
      return failing ? new Response("nope", { status: 503 }) : json({ objects: [] });
    },
  });
  standing(PRIVATE.conversation_id);
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

    await userEvent.click(await screen.findByRole("button", { name: "Open transcript" }));
    expect(await screen.findByText("No messages in this conversation yet.")).toBeTruthy();

    failing = true;
    await vi.advanceTimersByTimeAsync(30_000);
    expect(await vi.waitFor(() => screen.getByText("Error 503 — reload to retry."))).toBeTruthy();
    failing = false;
    await vi.advanceTimersByTimeAsync(30_000);
    expect(
      await vi.waitFor(() => screen.getByText("No messages in this conversation yet.")),
    ).toBeTruthy();
  } finally {
    vi.useRealTimers();
  }

  expect(screen.queryByRole("button", { name: "Open transcript" })).toBeNull();
  expect(posted.length).toBe(1);
});

test("a disclosure taken beside a homepage holds over a press of History and back", async () => {
  const posted: string[] = [];
  wire({
    "/transcript": () => json({ messages: [] }),
    "/read_private_transcript": (url) => {
      posted.push(url);
      return json({ applied: true, message: "Recorded." });
    },
    ...conversations([PRIVATE]),
  });
  standing(PRIVATE.conversation_id);
  render(<App agents={[HOSTING]} member={ADMIN} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Open transcript" }));
  expect(await screen.findByText("No messages in this conversation yet.")).toBeTruthy();

  const history = await screen.findByRole("button", { name: "History for Assistant" });
  await userEvent.click(history);
  await userEvent.click(history);

  expect(await screen.findByText("No messages in this conversation yet.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Open transcript" })).toBeNull();
  expect(posted.length).toBe(1);
});

test("leaving mid-acknowledgement does not open the transcript when the answer lands", async () => {
  let release: ((value: Response) => void) | null = null;
  wire({
    "/transcript": () => json({ messages: [] }),
    ...conversations([PRIVATE, { ...WALLED, disclosable: true }]),
    "/read_private_transcript": () =>
      new Promise<Response>((resolve) => {
        release = resolve;
      }),
  });
  standing(PRIVATE.conversation_id);
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Open transcript" }));
  await waitFor(() => expect(release).not.toBeNull());
  standing(WALLED.conversation_id);
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
    "/read_private_transcript": () => {
      if (release) return Response.json({ applied: false, message: "Refused." });
      return new Promise<Response>((resolve) => {
        release = resolve;
      });
    },
  });
  standing(PRIVATE.conversation_id);
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Open transcript" }));
  await waitFor(() => expect(release).not.toBeNull());
  standing(WALLED.conversation_id);
  await waitFor(async () => expect(await heldConversation()).toBe(ROOM));

  release!(Response.json({ applied: true, message: "Recorded." }));
  await new Promise((resolve) => setTimeout(resolve, 0));

  expect(screen.queryByText("No messages in this conversation yet.")).toBeNull();
  expect(screen.getByRole("button", { name: "Open transcript" })).toBeTruthy();
});

test("a refused acknowledgement states the refusal and opens nothing", async () => {
  const { calls } = wire({
    ...conversations([PRIVATE]),
    "/read_private_transcript": () => json({ applied: false, message: "Only an admin may read it." }),
  });
  standing(PRIVATE.conversation_id);
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Open transcript" }));

  await refusedNotice("Only an admin may read it.");
  expect(calls.some((url) => url.includes("/transcript"))).toBe(false);
});

test("a permalink to another member's conversation offers the listing's acknowledgement", async () => {
  location.hash = "#/c/" + CONVO_ID;
  const linked = { ...PRIVATE, conversation_id: CONVO_ID };
  const { calls } = wire({
    "/objects/conversation/": () => json(conversationDetail(linked)),
    "/read_private_transcript": () => json({ applied: true, message: "Recorded." }),
    "/actions/conversation/": () => json({ actions: [OPEN_TRANSCRIPT(CONVO_ID)] }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  expect(await screen.findByText(/private to owner@example.com/)).toBeTruthy();
  expect(calls.some((url) => url.includes("/transcript"))).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "Open transcript" }));

  expect(await screen.findByText("No messages in this conversation yet.")).toBeTruthy();
  expect(screen.queryByLabelText("Ask UFO")).toBeNull();
});

test("a permalink to a conversation naming no member is unshared, not missing", async () => {
  location.hash = "#/c/" + CONVO_ID;
  const linked = { ...WALLED, conversation_id: CONVO_ID };
  wire({
    "/objects/conversation/": () => json(conversationDetail(linked)),
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  expect(
    await screen.findByText("This conversation is not shared with this account."),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Open transcript" })).toBeNull();
});

test("a failed conversations read states the error rather than the composer", async () => {
  wire({ "/objects/conversation$": () => new Response("nope", { status: 503 }) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  expect((await screen.findAllByText("Error 503 — reload to retry.")).length).toBeGreaterThan(0);
  expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
});

test("the band names the conversation the acknowledgement is about, and reads nothing until it is taken", async () => {
  const { calls } = wire(conversations([PRIVATE]));
  standing(PRIVATE.conversation_id);
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  expect(await screen.findByRole("button", { name: "Open transcript" })).toBeTruthy();
  expect(await heldConversation()).toBe(OWNER);
  const pane = within(screen.getByRole("region", { name: "Assistant" }));
  expect(pane.getByRole("button", { name: "New" })).toBeTruthy();
  expect(calls.some((url) => url.includes("/transcript"))).toBe(false);
});
