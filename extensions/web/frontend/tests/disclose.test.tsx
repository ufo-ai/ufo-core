import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import { refusedNotice, AGENT, CONVO_ID, MEMBER, json, useStreamFake, wire } from "./harness";

const ADMIN = { ...MEMBER, admin: true };

const PRIVATE = {
  id: "c1",
  surface: "slack",
  member_email: "owner@example.com",
  description: "",
  speakers: [],
  turn_count: 3,
  created_at: "2026-07-30T10:00:00",
  last_turn_at: "2026-07-30T11:00:00",
  readable: false,
  disclosable: true,
};

const WALLED = { ...PRIVATE, id: "c2", disclosable: false };

const conversations = (entries: unknown[]) => ({
  "/conversations": () => json({ conversations: entries }),
});

/** A row the member may not read carries no words of its own, so `Private` is what marks the one
 *  an admin may acknowledge — and it is on the row itself, which is the control. */
const disclosable = () => screen.findByRole("button", { name: /Private/ });

beforeEach(() => {
  location.hash = "#/agents/" + AGENT.id + "/conversations";
  useStreamFake();
});

test("a disclosable row is the control, one shared with nobody is inert and says so", async () => {
  wire(conversations([PRIVATE, WALLED]));
  render(<App agents={[AGENT]} subagents={[]} member={ADMIN} newAgent={null} onAgents={() => {}} />);

  expect(await disclosable()).toBeTruthy();
  expect(screen.getByText(/Not shared with you/)).toBeTruthy();
  expect(screen.queryAllByRole("button", { name: /Not shared with you/ })).toEqual([]);
});

test("the acknowledgement names the owner and what opening records, and does not read yet", async () => {
  const { calls } = wire(conversations([PRIVATE]));
  render(<App agents={[AGENT]} subagents={[]} member={ADMIN} newAgent={null} onAgents={() => {}} />);

  await userEvent.click(await disclosable());

  const warning = await screen.findByText(/private to owner@example.com/);
  expect(warning.textContent).toContain("may contain private information");
  expect(warning.textContent).toContain("records your email, theirs, and the time");
  expect(warning.textContent).not.toContain("can read that record");
  expect(calls.some((url) => url.includes("/turns"))).toBe(false);
});

test("acknowledging posts the transcript intent and opens the conversation it named", async () => {
  const bodies: string[] = [];
  wire({
    "/turns": () => json({ turns: [], subagent_turns: [] }),
    "/files": () => json({ files: [] }),
    ...conversations([PRIVATE]),
    "/intents": (_url, init) => {
      bodies.push(String(init?.body));
      return json({ applied: true, message: "Recorded." });
    },
  });
  render(<App agents={[AGENT]} subagents={[]} member={ADMIN} newAgent={null} onAgents={() => {}} />);

  await userEvent.click(await disclosable());
  await userEvent.click(screen.getByRole("button", { name: "Open transcript" }));

  await waitFor(() => expect(bodies.length).toBe(1));
  expect(JSON.parse(bodies[0])).toEqual({
    verb: "read",
    kind: "transcript",
    conversation_id: "c1",
  });
  expect(await screen.findByText("No turns in this conversation yet.")).toBeTruthy();
  expect(screen.getByRole("heading", { name: "slack · owner@example.com" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Open transcript" })).toBeNull();
});

test("leaving mid-acknowledgement does not open the transcript when the answer lands", async () => {
  let release: ((value: Response) => void) | null = null;
  wire({
    "/turns": () => json({ turns: [], subagent_turns: [] }),
    "/files": () => json({ files: [] }),
    ...conversations([PRIVATE]),
    "/intents": () =>
      new Promise<Response>((resolve) => {
        release = resolve;
      }),
  });
  render(<App agents={[AGENT]} subagents={[]} member={ADMIN} newAgent={null} onAgents={() => {}} />);

  await userEvent.click(await disclosable());
  await userEvent.click(screen.getByRole("button", { name: "Open transcript" }));
  await waitFor(() => expect(release).not.toBeNull());
  await userEvent.click(screen.getByRole("button", { name: "All conversations" }));

  release!(Response.json({ applied: true, message: "Recorded." }));
  await new Promise((resolve) => setTimeout(resolve, 0));

  expect(screen.queryByText("No turns in this conversation yet.")).toBeNull();
  expect(screen.getByRole("button", { name: /Private/ })).toBeTruthy();
});

test("an acknowledgement in flight for one conversation never opens over another", async () => {
  let release: ((value: Response) => void) | null = null;
  wire({
    "/turns": () => json({ turns: [], subagent_turns: [] }),
    "/files": () => json({ files: [] }),
    ...conversations([PRIVATE, { ...WALLED, disclosable: true }]),
    "/intents": () => {
      if (release) return Response.json({ applied: false, message: "Refused." });
      return new Promise<Response>((resolve) => {
        release = resolve;
      });
    },
  });
  render(<App agents={[AGENT]} subagents={[]} member={ADMIN} newAgent={null} onAgents={() => {}} />);

  const openers = await screen.findAllByRole("button", { name: /Private/ });
  await userEvent.click(openers[0]);
  await userEvent.click(screen.getByRole("button", { name: "Open transcript" }));
  await waitFor(() => expect(release).not.toBeNull());
  await userEvent.click(screen.getByRole("button", { name: "All conversations" }));
  const again = await screen.findAllByRole("button", { name: /Private/ });
  await userEvent.click(again[1]);

  release!(Response.json({ applied: true, message: "Recorded." }));
  await new Promise((resolve) => setTimeout(resolve, 0));

  expect(screen.queryByText("No turns in this conversation yet.")).toBeNull();
  expect(screen.getByRole("button", { name: "Open transcript" })).toBeTruthy();
});

test("a refused acknowledgement states the refusal and opens nothing", async () => {
  const { calls } = wire({
    ...conversations([PRIVATE]),
    "/intents": () => json({ applied: false, message: "Only an admin may read it." }),
  });
  render(<App agents={[AGENT]} subagents={[]} member={ADMIN} newAgent={null} onAgents={() => {}} />);

  await userEvent.click(await disclosable());
  await userEvent.click(screen.getByRole("button", { name: "Open transcript" }));

  await refusedNotice("Only an admin may read it.");
  expect(calls.some((url) => url.includes("/turns"))).toBe(false);
});

test("a permalink to another member's conversation offers the listing's acknowledgement", async () => {
  location.hash = "#/c/" + CONVO_ID;
  const linked = { ...PRIVATE, id: CONVO_ID, agent_id: AGENT.id };
  const { calls } = wire({
    "/api/chats": (url) =>
      url.includes("conversation=")
        ? json({ chats: [], conversation: linked })
        : json({ chats: [] }),
    "/turns": () => json({ turns: [], subagent_turns: [] }),
    "/files": () => json({ files: [] }),
    "/intents": () => json({ applied: true, message: "Recorded." }),
  });
  render(<App agents={[AGENT]} subagents={[]} member={ADMIN} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByText(/private to owner@example.com/)).toBeTruthy();
  expect(calls.some((url) => url.includes("/turns"))).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "Open transcript" }));

  expect(await screen.findByText("No turns in this conversation yet.")).toBeTruthy();
  expect(
    screen.getByText("This conversation is read-only here. Reply in slack to continue it."),
  ).toBeTruthy();
});

test("a permalink to a conversation naming no member is unshared, not missing", async () => {
  location.hash = "#/c/" + CONVO_ID;
  const linked = { ...WALLED, id: CONVO_ID, agent_id: AGENT.id, member_email: null };
  wire({
    "/api/chats": (url) =>
      url.includes("conversation=")
        ? json({ chats: [], conversation: linked })
        : json({ chats: [] }),
  });
  render(<App agents={[AGENT]} subagents={[]} member={ADMIN} newAgent={null} onAgents={() => {}} />);

  expect(
    await screen.findByText("This conversation is not shared with this account."),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Open transcript" })).toBeNull();
});

test("leaving the acknowledgement returns to the listing without reading", async () => {
  wire(conversations([PRIVATE]));
  render(<App agents={[AGENT]} subagents={[]} member={ADMIN} newAgent={null} onAgents={() => {}} />);

  await userEvent.click(await disclosable());
  await userEvent.click(screen.getByRole("button", { name: "All conversations" }));

  expect(await disclosable()).toBeTruthy();
});
