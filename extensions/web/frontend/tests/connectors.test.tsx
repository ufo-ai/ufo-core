import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { workspaceHash } from "@/lib/route";

import {
  AGENT,
  AGENT_ID,
  MEMBER,
  SECOND,
  SECOND_ID,
  json,
  useStreamFake,
  wire,
} from "./harness";

beforeEach(() => {
  useStreamFake();
});

function grant(provider: string, shared: boolean, name: string) {
  return {
    provider,
    account_id: "acct",
    account_label: null,
    owner_email: "member@example.com",
    own: true,
    shared,
    connected_at: "2026-07-01T00:00:00",
    grant: name,
  };
}

function connectors() {
  return wire({
    "/connections": (url) =>
      json({
        connections: url.includes(SECOND_ID)
          ? [grant("notion", true, "g2")]
          : [grant("github", false, "g1")],
      }),
    "/github/coverage": () =>
      json({ api: true, git_push: false, sources: true }),
    "/transcript": () => json({ messages: [] }),
  });
}

test("the workspace tab lists the connection pool", async () => {
  location.hash = "#/workspace/connectors";
  connectors();
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByText("github")).toBeTruthy();
  expect(screen.getByText("Only you")).toBeTruthy();
  expect(screen.getByText("API")).toBeTruthy();
  expect(screen.getByText("Not connected")).toBeTruthy();
  expect(screen.getAllByText("Sources").length).toBeGreaterThan(1);
});

test("the agent tab reads attached connections", async () => {
  location.hash = "#/agents/" + AGENT_ID + "/connectors";
  const { calls } = connectors();
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByText("github")).toBeTruthy();
  expect(calls.some((url) => url.includes("/agents/" + AGENT_ID + "/connections"))).toBe(true);
});

test("a grant change is admitted into the lane of the agent whose tab holds it", async () => {
  const posted: string[] = [];
  location.hash = "#/agents/" + AGENT_ID + "/connectors";
  wire({
    "/connections": () => json({ connections: [grant("github", false, "g1")] }),
    "/intents": (url) => {
      posted.push(url);
      return json({ applied: true, message: "Shared." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Share with agent" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toContain("/agents/" + AGENT_ID + "/intents");
});

test("an empty pool states that no connector is connected yet", async () => {
  location.hash = workspaceHash("connectors");
  wire({
    "/connections": () => json({ connections: [] }),
    "/github/coverage": () => json({ api: false, git_push: false, sources: false }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByText("No connector is connected yet.")).toBeTruthy();
});

test("the bar is drawn while the first read is still in flight", async () => {
  const pending = new Map<string, (value: Response) => void>();
  location.hash = "#/agents/" + AGENT_ID + "/connectors";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/connections")) {
        return new Promise<Response>((resolve) => {
          pending.set(url, resolve);
        });
      }
      return json({ chats: [] });
    }),
  );
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByRole("combobox", { name: "Connection" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Add connector" })).toBeTruthy();
  expect(pending.size).toBe(2);
});

test("the agent's own tab states what is shared with that agent", async () => {
  location.hash = "#/agents/" + AGENT_ID + "/connectors";
  wire({
    "/connections": () => json({ connections: [grant("github", true, "g1")] }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByText("github")).toBeTruthy();
  expect(screen.queryByRole("combobox", { name: "Agent" })).toBeNull();
});
