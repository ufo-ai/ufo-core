import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { MainAgentProvider } from "@/lib/mainAgent";

import {
  PlacedWorkspace,
  AGENT,
  MEMBER,
  SETTINGS,
  TASK_KIND,
  TRIGGER_KIND,
  json,
  openAgentSettings,
  objectIndex,
  useStreamFake,
} from "./harness";

const STALE_CONNECTION = {
  provider: "stale-provider",
  account_id: null,
  owner_email: null,
  shared: true,
  connected_at: "2026-07-30T12:00:00",
  grant: "g1",
};

beforeEach(() => {
  useStreamFake();
});

test("leaving a view discards the read left behind rather than painting it", async () => {
  let releaseConnectors: ((value: Response) => void) | null = null;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/connections")) {
        return new Promise<Response>((resolve) => {
          releaseConnectors = resolve;
        });
      }
      if (url.includes("/settings")) return json(SETTINGS);
      if (url.includes("/workspace/radar")) return json({ runs: [] });
      if (url.includes("/objects/scheduled_task")) return objectIndex(TASK_KIND, []);
      if (url.includes("/objects/source_trigger")) return objectIndex(TRIGGER_KIND, []);
      if (url.includes("/objects/conversation")) return json({ objects: [] });
      if (url.includes("/api/chats")) return json({ chats: [] });
      if (url.includes("/api/agents/status")) return json({ statuses: [] });
      return json({ messages: [] });
    }),
  );

  location.hash = "#/agents/" + AGENT.id;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await openAgentSettings("Assistant", "Connectors");

  await waitFor(() => expect(releaseConnectors).not.toBeNull());

  await userEvent.keyboard("{Escape}");
  const sidebar = within(screen.getByRole("navigation", { name: "Workspace" }));
  await userEvent.click(sidebar.getByRole("button", { name: "New conversation" }));
  expect(await screen.findByLabelText("Ask anything")).toBeTruthy();

  releaseConnectors!(json({ connections: [STALE_CONNECTION] }));
  await new Promise((resolve) => setTimeout(resolve, 0));

  expect(screen.queryByText("stale-provider")).toBeNull();
  expect(screen.getByLabelText("Ask anything")).toBeTruthy();
});

test("a slow read for a filter the member left never paints over the filter they chose", async () => {
  const pending = new Map<string, (value: Response) => void>();
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (url: string) =>
        new Promise<Response>((resolve) => {
          pending.set(url, resolve);
        }),
    ),
  );

  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedWorkspace view="memory" />
    </MainAgentProvider>,
  );

  const matches = (text: string, kind: string) =>
    json({
      available: true,
      kinds: ["fact", "preference"],
      matches: [{ text, kind, ref: null, created_at: null }],
      older: null,
      newer: null,
    });

  await waitFor(() => expect(pending.size).toBe(1));
  pending.get([...pending.keys()][0])!(
    json({ available: true, kinds: ["fact", "preference"], matches: [], older: null, newer: null }),
  );
  await screen.findByText("No memories yet.");

  await userEvent.click(screen.getByRole("tab", { name: "Fact" }));
  await waitFor(() => expect(pending.size).toBe(2));
  const slow = [...pending.keys()].find((url) => url.includes("kind=fact"))!;

  await userEvent.type(screen.getByPlaceholderText("Search"), "dark{Enter}");
  await waitFor(() => expect(pending.size).toBe(3));
  const chosen = [...pending.keys()].find((url) => url.includes("q=dark"))!;

  pending.get(chosen)!(matches("prefers dark mode", "preference"));
  expect(await screen.findByText("prefers dark mode")).toBeTruthy();

  pending.get(slow)!(matches("stale fact", "fact"));
  await new Promise((resolve) => setTimeout(resolve, 0));

  expect(screen.queryByText("stale fact")).toBeNull();
  expect(screen.getByText("prefers dark mode")).toBeTruthy();
});
