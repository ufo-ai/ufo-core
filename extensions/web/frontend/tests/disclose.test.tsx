import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import { refusedNotice, AGENT, MEMBER, json, useStreamFake, wire } from "./harness";

const ADMIN = { ...MEMBER, admin: true };

const PRIVATE = {
  id: "c1",
  surface: "slack",
  member_email: "owner@example.com",
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

beforeEach(() => {
  location.hash = "#/agents/" + AGENT.id + "/conversations";
  useStreamFake();
});

test("a disclosable conversation offers admin opening, one that is not says so", async () => {
  wire(conversations([PRIVATE, WALLED]));
  render(<App agents={[AGENT]} member={ADMIN} />);

  expect(await screen.findByRole("button", { name: "Open as admin" })).toBeTruthy();
  expect(screen.getByText("not shared with you")).toBeTruthy();
});

test("the acknowledgement names the owner and what opening records, and does not read yet", async () => {
  const { calls } = wire(conversations([PRIVATE]));
  render(<App agents={[AGENT]} member={ADMIN} />);

  await userEvent.click(await screen.findByRole("button", { name: "Open as admin" }));

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
  render(<App agents={[AGENT]} member={ADMIN} />);

  await userEvent.click(await screen.findByRole("button", { name: "Open as admin" }));
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
  render(<App agents={[AGENT]} member={ADMIN} />);

  await userEvent.click(await screen.findByRole("button", { name: "Open as admin" }));
  await userEvent.click(screen.getByRole("button", { name: "Open transcript" }));
  await waitFor(() => expect(release).not.toBeNull());
  await userEvent.click(screen.getByRole("button", { name: "All conversations" }));

  release!(Response.json({ applied: true, message: "Recorded." }));
  await new Promise((resolve) => setTimeout(resolve, 0));

  expect(screen.queryByText("No turns in this conversation yet.")).toBeNull();
  expect(screen.getByRole("button", { name: "Open as admin" })).toBeTruthy();
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
  render(<App agents={[AGENT]} member={ADMIN} />);

  const openers = await screen.findAllByRole("button", { name: "Open as admin" });
  await userEvent.click(openers[0]);
  await userEvent.click(screen.getByRole("button", { name: "Open transcript" }));
  await waitFor(() => expect(release).not.toBeNull());
  await userEvent.click(screen.getByRole("button", { name: "All conversations" }));
  const again = await screen.findAllByRole("button", { name: "Open as admin" });
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
  render(<App agents={[AGENT]} member={ADMIN} />);

  await userEvent.click(await screen.findByRole("button", { name: "Open as admin" }));
  await userEvent.click(screen.getByRole("button", { name: "Open transcript" }));

  await refusedNotice("Only an admin may read it.");
  expect(calls.some((url) => url.includes("/turns"))).toBe(false);
});

test("leaving the acknowledgement returns to the listing without reading", async () => {
  wire(conversations([PRIVATE]));
  render(<App agents={[AGENT]} member={ADMIN} />);

  await userEvent.click(await screen.findByRole("button", { name: "Open as admin" }));
  await userEvent.click(screen.getByRole("button", { name: "All conversations" }));

  expect(await screen.findByRole("button", { name: "Open as admin" })).toBeTruthy();
});
