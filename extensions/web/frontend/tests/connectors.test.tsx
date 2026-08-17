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
  fact,
  json,
  pageFits,
  pick,
  pressRow,
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
    agents: [],
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

test("the pool narrows on the header's search, which names what it searches", async () => {
  location.hash = "#/workspace/connectors";
  wire({
    "/connections": () =>
      json({ connections: [grant("github", false, "g1"), grant("notion", true, "g2")] }),
    "/github/coverage": () => json({ api: true, git_push: false, sources: true }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByText("notion")).toBeTruthy();
  const box = screen.getByLabelText("Search connectors");
  expect(screen.getByRole("heading", { level: 1, name: "Workspace" }).parentElement!.contains(box)).toBe(
    true,
  );

  await userEvent.type(box, "github{enter}");

  await waitFor(() => expect(screen.queryByText("notion")).toBeNull());
  expect(screen.getByText("github")).toBeTruthy();
  expect(location.hash).toContain("q=github");
});

test("the pool fits the desktop it is read on, so the row's act never scrolls off", async () => {
  location.hash = "#/workspace/connectors";
  connectors();
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  const across = (await screen.findByText("github")).closest("table")!.style.minWidth;
  expect(pageFits(across)).toBe(true);
  expect(across).toBe(
    "calc(2 * var(--size-fact-column) + 2 * var(--size-prose-column) + 1 * var(--size-act))",
  );
});

test("the agent's own edges fit that same desktop", async () => {
  location.hash = "#/agents/" + AGENT_ID + "/connectors";
  connectors();
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  const across = (await screen.findByText("github")).closest("table")!.style.minWidth;
  expect(pageFits(across)).toBe(true);
  expect(across).toBe(
    "calc(2 * var(--size-fact-column) + 1 * var(--size-prose-column) + 1 * var(--size-act))",
  );
});

test("the agent tab reads attached connections", async () => {
  location.hash = "#/agents/" + AGENT_ID + "/connectors";
  const { calls } = connectors();
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByText("github")).toBeTruthy();
  expect(calls.some((url) => url.includes("/agents/" + AGENT_ID + "/connections"))).toBe(true);
});

test("the pool's record states what the row gave up, and attaches to the agent named on it", async () => {
  const posted: string[] = [];
  location.hash = "#/workspace/connectors";
  wire({
    "/connections": () => json({ connections: [grant("github", false, "g1")] }),
    "/github/coverage": () => json({ api: true, git_push: false, sources: true }),
    "/intents": (url) => {
      posted.push(url);
      return json({ applied: true, message: "Attached." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await pressRow("github");

  expect(fact("Owner")).toBe("You");
  expect(fact("Connected")).toBe("Jul 1 2026");

  await pick("Agent", "second");
  await userEvent.click(screen.getByRole("button", { name: "Attach to agent" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toContain("/agents/" + SECOND_ID + "/intents");
});

test("the pool's record shares and revokes into the lane of the agent already holding the grant", async () => {
  const posted: string[] = [];
  location.hash = "#/workspace/connectors";
  wire({
    "/connections": () =>
      json({
        connections: [{ ...grant("github", true, "g1"), agents: [{ id: SECOND_ID, name: "second" }] }],
      }),
    "/github/coverage": () => json({ api: true, git_push: false, sources: true }),
    "/intents": (url) => {
      posted.push(url);
      return json({ applied: true, message: "Applied." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await pressRow("github");
  await userEvent.click(screen.getByRole("button", { name: "Unshare" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toContain("/agents/" + SECOND_ID + "/intents");

  await userEvent.click(await screen.findByRole("button", { name: "Revoke" }));
  await userEvent.click(screen.getByRole("button", { name: "Confirm revoke" }));

  await waitFor(() => expect(posted.length).toBe(2));
  expect(posted[1]).toContain("/agents/" + SECOND_ID + "/intents");
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

  await pressRow("github");

  expect(fact("Owner")).toBe("You");
  expect(fact("Connected")).toBe("Jul 1 2026");

  await userEvent.click(screen.getByRole("button", { name: "Share with agent" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toContain("/agents/" + AGENT_ID + "/intents");
});

test("the agent's record revokes the grant it stands on", async () => {
  const posted: string[] = [];
  location.hash = "#/agents/" + AGENT_ID + "/connectors";
  wire({
    "/connections": () => json({ connections: [grant("github", true, "g1")] }),
    "/intents": (url) => {
      posted.push(url);
      return json({ applied: true, message: "Revoked." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await pressRow("github");
  await userEvent.click(screen.getByRole("button", { name: "Revoke" }));
  await userEvent.click(screen.getByRole("button", { name: "Confirm revoke" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toContain("/agents/" + AGENT_ID + "/intents");
});

test("a revoked connection stays shut when the grant comes back on a later read", async () => {
  let served = 0;
  location.hash = "#/agents/" + AGENT_ID + "/connectors";
  wire({
    "/connections": (url) => {
      if (!url.includes("/agents/"))
        return json({ connections: [{ ...grant("github", true, "g1"), agents: [] }] });
      served += 1;
      return json({ connections: served === 2 ? [] : [grant("github", true, "g1")] });
    },
    "/intents": () => json({ applied: true, message: "Applied." }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await pressRow("github");
  expect(await screen.findByRole("complementary", { name: "acct" })).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Revoke" }));
  await userEvent.click(screen.getByRole("button", { name: "Confirm revoke" }));
  await waitFor(() => expect(screen.queryByRole("complementary", { name: "acct" })).toBeNull());

  await pick("Connection", "acct");
  await userEvent.click(screen.getByRole("button", { name: "Attach" }));

  expect(await screen.findByText("github")).toBeTruthy();
  expect(screen.queryByRole("complementary", { name: "acct" })).toBeNull();
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
