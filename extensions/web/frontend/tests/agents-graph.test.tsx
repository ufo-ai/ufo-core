import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import { AGENT, AGENT_ID, MEMBER, SECOND_ID, json, useStreamFake, wire } from "./harness";

const RESEARCH = { id: SECOND_ID, name: "research", model: "claude-opus-4-8", main: false };
const SUBAGENTS = [
  { name: "deep_research", model: "claude-opus-4-8" },
  { name: "general_purpose", model: null },
];
const INSTALLATIONS = {
  installations: [
    { surface: "slack", agent_id: AGENT_ID },
    { surface: "ufo", agent_id: AGENT_ID },
  ],
};
const CONNECTIONS = {
  connections: [
    {
      provider: "github",
      account_id: "acct-1",
      account_label: "metalcraft",
      owner_email: MEMBER.email,
      own: true,
      shared: true,
      connected_at: "2026-08-01T09:00:00.000Z",
      grant: "g1",
      agents: [{ id: AGENT_ID, name: "assistant" }],
    },
  ],
};
const SOURCES = {
  sources: [
    {
      name: "support-mail",
      backend: "gmail",
      stream: "messages",
      account_id: null,
      base_url: null,
      backfill_days: null,
      owner_email: MEMBER.email,
      own: true,
      shared: true,
      consecutive_errors: 0,
      next_sync_at: "2026-08-17T00:00:00.000Z",
    },
  ],
};

function wireGraph() {
  return wire({
    "/workspace/surfaces": () => json(INSTALLATIONS),
    "/workspace/sources": () => json(SOURCES),
    "/connections": () => json(CONNECTIONS),
    "/transcript": () => json({ messages: [] }),
  });
}

function renderAgents() {
  return render(
    <App
      agents={[AGENT, RESEARCH]}
      subagents={SUBAGENTS}
      member={MEMBER}
      newAgent={null}
      onAgents={() => {}}
    />,
  );
}

async function openGraph(): Promise<HTMLElement> {
  await userEvent.click(screen.getByRole("button", { name: "Agents" }));
  await userEvent.click(await screen.findByRole("tab", { name: "Graph" }));
  return screen.findByRole("group", { name: "Agent topology" });
}

/** The control wrapping a label — a button where a record opens, a link where a workspace page
 *  states the rest. */
function node(graph: HTMLElement, label: string): HTMLElement {
  const control = within(graph).getByText(label).closest("button, a");
  if (!control) throw new Error("no node labelled " + label);
  return control as HTMLElement;
}

/** The drawn node holding a label — what focus and search dim as one thing. A tile row resolves
 *  to its tile. */
function box(graph: HTMLElement, label: string): HTMLElement {
  const drawn = within(graph).getByText(label).closest("[data-node]");
  if (!drawn) throw new Error("no drawn node holding " + label);
  return drawn as HTMLElement;
}

function edges(kind: string): number {
  return document.querySelectorAll(`[data-edge="${kind}"]`).length;
}

beforeEach(() => {
  location.hash = "";
  useStreamFake();
});

test("the toggle turns the list into the topology graph and holds the choice", async () => {
  const { calls } = wireGraph();
  const first = renderAgents();
  await userEvent.click(screen.getByRole("button", { name: "Agents" }));
  expect(await screen.findByRole("table")).toBeTruthy();

  await userEvent.click(screen.getByRole("tab", { name: "Graph" }));

  expect(await screen.findByRole("group", { name: "Agent topology" })).toBeTruthy();
  expect(screen.queryByRole("table")).toBeNull();
  expect(screen.queryByRole("tab", { name: "All" })).toBeNull();
  expect(localStorage.getItem("agents-view")).toBe("graph");

  const reads = calls.filter((url) => url.includes("/connections")).length;
  await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
  await waitFor(() =>
    expect(calls.filter((url) => url.includes("/connections")).length).toBe(reads + 1),
  );

  first.unmount();
  renderAgents();
  await userEvent.click(screen.getByRole("button", { name: "Agents" }));

  expect(await screen.findByRole("group", { name: "Agent topology" })).toBeTruthy();
  expect(screen.queryByRole("table")).toBeNull();
});

test("the graph states what the workspace runs and what each agent reaches", async () => {
  wireGraph();
  renderAgents();
  const graph = await openGraph();

  expect(within(graph).getByText("Portal")).toBeTruthy();
  expect(within(graph).getByText("Slack")).toBeTruthy();
  expect(within(graph).getByText("Terminal")).toBeTruthy();
  expect(node(graph, "assistant").tagName).toBe("BUTTON");
  expect(node(graph, "research").tagName).toBe("BUTTON");
  expect(node(graph, "deep_research").tagName).toBe("BUTTON");
  expect(node(graph, "general_purpose").tagName).toBe("BUTTON");
  expect(within(graph).getByText("Subagents")).toBeTruthy();
  expect(node(graph, "Memory").getAttribute("href")).toBe("#/workspace/memory");
  expect(within(graph).getByText("metalcraft")).toBeTruthy();
  expect(node(graph, "github").getAttribute("href")).toBe("#/workspace/connectors");
  expect(node(graph, "support-mail").getAttribute("href")).toBe("#/workspace/sources");

  expect(edges("surface")).toBe(4);
  expect(edges("connector")).toBe(1);
  expect(edges("memory")).toBe(2);
  expect(edges("subagent")).toBe(2);
});

test("hovering a node holds its own reach lit and dims the rest", async () => {
  wireGraph();
  renderAgents();
  const graph = await openGraph();

  await userEvent.hover(node(graph, "research"));

  expect(box(graph, "assistant").className).toContain("opacity-40");
  expect(box(graph, "github").className).toContain("opacity-40");
  expect(box(graph, "Portal").className).not.toContain("opacity-40");
  expect(box(graph, "Memory").className).not.toContain("opacity-40");
  expect(box(graph, "deep_research").className).not.toContain("opacity-40");
  const connector = document.querySelector('[data-edge="connector"]');
  expect(connector?.getAttribute("stroke-opacity")).toBe("0.12");

  await userEvent.unhover(node(graph, "research"));

  expect(box(graph, "assistant").className).not.toContain("opacity-40");
  expect(connector?.getAttribute("stroke-opacity")).toBe("0.5");
});

test("a graph node opens the record its list row opens", async () => {
  wireGraph();
  renderAgents();
  const graph = await openGraph();

  await userEvent.click(node(graph, "research"));

  expect(location.hash).toBe("#/agents/" + SECOND_ID);
  expect(await screen.findByRole("complementary", { name: "research" })).toBeTruthy();

  await userEvent.click(node(graph, "deep_research"));

  expect(location.hash).toBe("#/subagents/deep_research");
  expect(await screen.findByRole("complementary", { name: "deep_research" })).toBeTruthy();
});

test("the search dims the nodes it does not match", async () => {
  wireGraph();
  renderAgents();
  const graph = await openGraph();

  await userEvent.type(screen.getByLabelText("Search agents"), "res");

  expect(box(graph, "research").className).not.toContain("opacity-40");
  expect(node(graph, "deep_research").className).not.toContain("opacity-40");
  expect(box(graph, "assistant").className).toContain("opacity-40");
  expect(box(graph, "github").className).toContain("opacity-40");
  expect(node(graph, "support-mail").className).toContain("opacity-40");
});
