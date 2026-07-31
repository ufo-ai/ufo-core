import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { MainAgentProvider } from "@/lib/mainAgent";
import { Workspace } from "@/views/Workspace";

import { AGENT, MEMBER, SECOND, json, useStreamFake } from "./harness";

beforeEach(() => {
  useStreamFake();
});

test("switching tabs discards the read left behind rather than painting it", async () => {
  let releaseSkills: ((value: Response) => void) | null = null;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/skills")) {
        return new Promise<Response>((resolve) => {
          releaseSkills = resolve;
        });
      }
      if (url.includes("/tasks")) return json({ tasks: [], spec_schema: null });
      return json({ messages: [] });
    }),
  );

  render(<App agents={[AGENT]} member={MEMBER} />);

  await userEvent.click(screen.getByRole("tab", { name: "skills" }));
  await waitFor(() => expect(releaseSkills).not.toBeNull());

  await userEvent.click(screen.getByRole("tab", { name: "tasks" }));
  expect(await screen.findByText("No scheduled tasks for this agent.")).toBeTruthy();

  releaseSkills!(json({ skills: [{ name: "stale", description: "stale skill", origin: "member" }] }));
  await new Promise((resolve) => setTimeout(resolve, 0));

  expect(screen.queryByText("stale skill")).toBeNull();
  expect(screen.getByText("No scheduled tasks for this agent.")).toBeTruthy();
});

test("switching agents discards the read left behind rather than painting it", async () => {
  const pending = new Map<string, (value: Response) => void>();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/skills")) {
        return new Promise<Response>((resolve) => {
          pending.set(url, resolve);
        });
      }
      return json({ messages: [] });
    }),
  );

  render(<App agents={[AGENT, SECOND]} member={MEMBER} />);

  await userEvent.click(screen.getByRole("tab", { name: "skills" }));
  await waitFor(() => expect(pending.size).toBe(1));
  const [firstUrl] = [...pending.keys()];

  await userEvent.click(screen.getByRole("button", { name: /second/ }));
  await userEvent.click(screen.getByRole("tab", { name: "skills" }));
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
      <Workspace view="memory" />
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

  await userEvent.click(screen.getByRole("button", { name: "fact" }));
  await waitFor(() => expect(pending.size).toBe(2));
  const slow = [...pending.keys()].find((url) => url.includes("kind=fact"))!;

  await userEvent.type(screen.getByPlaceholderText("Search memory…"), "dark");
  await userEvent.click(screen.getByRole("button", { name: "Search" }));
  await waitFor(() => expect(pending.size).toBe(3));
  const chosen = [...pending.keys()].find((url) => url.includes("q=dark"))!;

  pending.get(chosen)!(matches("prefers dark mode", "preference"));
  expect(await screen.findByText("prefers dark mode")).toBeTruthy();

  pending.get(slow)!(matches("stale fact", "fact"));
  await new Promise((resolve) => setTimeout(resolve, 0));

  expect(screen.queryByText("stale fact")).toBeNull();
  expect(screen.getByText("prefers dark mode")).toBeTruthy();
});
