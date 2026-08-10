import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { MainAgentProvider } from "@/lib/mainAgent";

import {
  PlacedCustomize,
  AGENT,
  MEMBER,
  NO_TASKS,
  SECOND,
  TASK_KIND,
  json,
  objectIndex,
  pick,
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

test("switching tabs discards the read left behind rather than painting it", async () => {
  let releaseConnectors: ((value: Response) => void) | null = null;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/connections")) {
        return new Promise<Response>((resolve) => {
          releaseConnectors = resolve;
        });
      }
      if (url.includes("/objects/scheduled_task")) return objectIndex(TASK_KIND, []);
      if (url.includes("/api/chats")) return json({ chats: [] });
      return json({ messages: [] });
    }),
  );

  location.hash = "#/agents/" + AGENT.id + "/connectors";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  await waitFor(() => expect(releaseConnectors).not.toBeNull());

  await userEvent.click(screen.getByRole("tab", { name: "Scheduled" }));
  expect(await screen.findByText(NO_TASKS)).toBeTruthy();

  releaseConnectors!(json({ connections: [STALE_CONNECTION] }));
  await new Promise((resolve) => setTimeout(resolve, 0));

  expect(screen.queryByText("stale-provider")).toBeNull();
  expect(screen.getByText(NO_TASKS)).toBeTruthy();
});

test("picking another agent discards the read left behind rather than painting it", async () => {
  const pending = new Map<string, (value: Response) => void>();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/skills")) {
        return new Promise<Response>((resolve) => {
          pending.set(url, resolve);
        });
      }
      if (url.includes("/api/chats")) return json({ chats: [] });
      return json({ messages: [] });
    }),
  );

  location.hash = "#/customize/skills";
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} />);

  await waitFor(() => expect(pending.size).toBe(1));
  const [firstUrl] = [...pending.keys()];

  await pick("Agent", "second");
  await waitFor(() => expect(pending.size).toBe(2));
  const secondUrl = [...pending.keys()].find((url) => url !== firstUrl)!;

  pending.get(secondUrl)!(
    json({ skills: [{ name: "mine", description: "second's skill", origin: "member" }] }),
  );
  await waitFor(() => expect(screen.getByText("second's skill")).toBeTruthy());

  pending.get(firstUrl)!(
    json({ skills: [{ name: "theirs", description: "first's skill", origin: "member" }] }),
  );
  await new Promise((resolve) => setTimeout(resolve, 0));

  expect(screen.queryByText("first's skill")).toBeNull();
  expect(screen.getByText("second's skill")).toBeTruthy();
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
      <PlacedCustomize view="memory" />
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
