import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { customizeHash } from "@/lib/route";

import {
  AGENT,
  AGENT_ID,
  MEMBER,
  SECOND,
  SECOND_ID,
  json,
  opened,
  pick,
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
    owner_email: "member@example.com",
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
    "/transcript": () => json({ messages: [] }),
  });
}

test("the customize tab lists every grant on the agent it names", async () => {
  location.hash = "#/customize/connectors";
  connectors();
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} />);

  expect(await screen.findByText("github")).toBeTruthy();
  expect(screen.getByText("Private")).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Connectors" }).getAttribute("aria-selected")).toBe(
    "true",
  );
  expect((await opened("Agent")).map((option) => option.textContent)).toEqual([
    "assistant",
    "second",
  ]);
});

test("picking an agent reads that agent's grants and names it in the hash", async () => {
  location.hash = "#/customize/connectors";
  const { calls } = connectors();
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} />);

  expect(await screen.findByText("github")).toBeTruthy();

  await pick("Agent", "second");

  expect(await screen.findByText("notion")).toBeTruthy();
  expect(screen.queryByText("github")).toBeNull();
  expect(location.hash).toBe("#/customize/connectors?agent=" + SECOND_ID);
  expect(calls.some((url) => url.includes("/agents/" + SECOND_ID + "/connections"))).toBe(true);
});

test("a grant change is admitted into the lane of the agent the member picked", async () => {
  const posted: string[] = [];
  location.hash = "#/customize/connectors";
  wire({
    "/connections": () => json({ connections: [grant("github", false, "g1")] }),
    "/intents": (url) => {
      posted.push(url);
      return json({ applied: true, message: "Shared." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} />);

  await pick("Agent", "second");
  await userEvent.click(await screen.findByRole("button", { name: "Share with agent" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toContain("/agents/" + SECOND_ID + "/intents");
});

test("a reloaded pick lands on that agent, which the empty line names", async () => {
  location.hash = customizeHash("connectors", { agent: SECOND_ID });
  wire({
    "/connections": () => json({ connections: [] }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} />);

  expect(await screen.findByText("No account is connected to second yet.")).toBeTruthy();
});

test("a hash naming an agent this member cannot reach reports it", async () => {
  location.hash = customizeHash("connectors", { agent: "99999999-9999-4999-8999-999999999999" });
  const { calls } = connectors();
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} />);

  expect(await screen.findByText("No such agent.")).toBeTruthy();
  expect(calls.some((url) => url.includes("/connections"))).toBe(false);
});

test("the bar is drawn while the first read is still in flight", async () => {
  const pending = new Map<string, (value: Response) => void>();
  location.hash = "#/customize/connectors";
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
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} />);

  expect(await screen.findByRole("combobox", { name: "Agent" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Add connector" })).toBeTruthy();
  expect(pending.size).toBe(1);
});

test("the agent's own tab states what is shared with that agent", async () => {
  location.hash = "#/agents/" + AGENT_ID + "/connectors";
  wire({
    "/connections": () => json({ connections: [grant("github", true, "g1")] }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  expect(await screen.findByText("github")).toBeTruthy();
  expect(screen.queryByRole("combobox", { name: "Agent" })).toBeNull();
});
