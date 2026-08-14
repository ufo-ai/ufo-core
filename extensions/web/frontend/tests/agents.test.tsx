import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { Portal } from "@/Portal";

import {
  AGENT,
  AGENT_ID,
  MEMBER,
  SECOND_ID,
  json,
  opened,
  pressRow,
  useStreamFake,
  wire,
} from "./harness";

/** The `agent` kind's own schema, as `AgentSpec.model_json_schema()` serves it with `prompt` added
 *  to the required list — the descriptions included, because a member must never read one. */
const SPEC_SCHEMA = {
  additionalProperties: false,
  properties: {
    model: {
      description: "The model id the agent runs on, or 'auto' to follow the deploy's model.",
      title: "Model",
      type: "string",
    },
    internet_access_allowed: {
      description: "Whether this agent may use the deploy's sandbox public-internet capability.",
      title: "Internet Access Allowed",
      type: "boolean",
    },
    reasoning: {
      description: "Reasoning effort for the agent's turns.",
      enum: ["auto", "off", "low", "medium", "high"],
      title: "Reasoning",
      type: "string",
    },
    prompt: {
      anyOf: [{ type: "string" }, { type: "null" }],
      default: null,
      description: "The agent's system prompt — accepted only when creating an agent.",
      title: "Prompt",
    },
  },
  required: ["model", "internet_access_allowed", "reasoning", "prompt"],
  title: "AgentSpec",
  type: "object",
};

const NEW_AGENT = { spec_schema: SPEC_SCHEMA, models: ["claude-opus-4-8", "claude-sonnet-5"] };
const ADMIN = { ...MEMBER, admin: true };
const RESEARCH = { id: SECOND_ID, name: "research", model: "claude-opus-4-8", main: false };
const PROMPT = "Answer with sources.";

function boot(agents: unknown[], member: unknown, newAgent: unknown) {
  return json({ member, agents, subagents: [], new_agent: newAgent });
}

async function openCreate() {
  await userEvent.click(await screen.findByRole("button", { name: "Agents" }));
  await userEvent.click(await screen.findByRole("button", { name: "New agent" }));
  return screen.findByLabelText("Name");
}

async function fill(name: string) {
  await userEvent.type(await screen.findByLabelText("Name"), name);
  await userEvent.type(screen.getByLabelText("Prompt"), PROMPT);
  await userEvent.click(screen.getByRole("button", { name: "Create" }));
}

beforeEach(() => {
  location.hash = "";
  useStreamFake();
});

test("an admin creates an agent from the bar, and the workspace answers with it", async () => {
  const posted: { url: string; body: string }[] = [];
  let landed = false;
  wire({
    "/api/agents": () => boot(landed ? [AGENT, RESEARCH] : [AGENT], ADMIN, NEW_AGENT),
    "/intents": (url, init) => {
      posted.push({ url, body: String(init?.body) });
      landed = true;
      return json({ applied: true, message: "Saved." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  render(<Portal />);

  await openCreate();
  await fill("research");

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0].url).toBe("/surface/web/agents/" + AGENT_ID + "/intents");
  expect(JSON.parse(posted[0].body)).toEqual({
    verb: "apply",
    kind: "agent",
    name: "research",
    spec: {
      model: "claude-opus-4-8",
      internet_access_allowed: false,
      reasoning: "auto",
      prompt: PROMPT,
    },
  });

  await waitFor(() => expect(screen.queryByLabelText("Name")).toBeNull());
  expect(await screen.findByText("Created research.")).toBeTruthy();

  await pressRow("research");

  expect(location.hash).toBe("#/agents/" + SECOND_ID);
  expect(screen.queryByText("No such agent.")).toBeNull();
  expect(await screen.findByRole("complementary", { name: "research" })).toBeTruthy();
});

test("a refused create keeps the panel standing with what the member typed", async () => {
  wire({
    "/api/agents": () => boot([AGENT], ADMIN, NEW_AGENT),
    "/intents": () => json({ applied: false, message: "An agent named 'research' already exists." }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<Portal />);

  await openCreate();
  await fill("research");

  expect(await screen.findByText("An agent named 'research' already exists.")).toBeTruthy();
  expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("research");
  expect((screen.getByLabelText("Prompt") as HTMLTextAreaElement).value).toBe(PROMPT);
  expect(screen.queryByText("Created research.")).toBeNull();
});

test("a field the kind requires holds the act, and closing the panel leaves without one", async () => {
  const posted: string[] = [];
  wire({
    "/api/agents": () => boot([AGENT], ADMIN, NEW_AGENT),
    "/intents": (_url, init) => {
      posted.push(String(init?.body));
      return json({ applied: true, message: "Saved." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  render(<Portal />);

  await openCreate();
  await userEvent.type(await screen.findByLabelText("Name"), "research");
  await userEvent.click(screen.getByRole("button", { name: "Create" }));

  expect(posted).toEqual([]);
  expect(screen.getByLabelText("Name")).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Close" }));

  await waitFor(() => expect(screen.queryByLabelText("Name")).toBeNull());
  expect(posted).toEqual([]);
});

test("the form draws the kind's schema and never the prose written for the agent", async () => {
  wire({
    "/api/agents": () => boot([AGENT], ADMIN, NEW_AGENT),
    "/transcript": () => json({ messages: [] }),
  });
  render(<Portal />);

  await openCreate();

  expect(document.querySelectorAll("h1").length).toBe(1);
  expect(screen.getByLabelText("Internet Access Allowed")).toBeTruthy();
  expect((screen.getByLabelText("Prompt") as HTMLTextAreaElement).required).toBe(true);
  expect(screen.queryByText(/sandbox public-internet capability/)).toBeNull();
  expect(screen.queryByText(/accepted only when creating an agent/)).toBeNull();
  expect(screen.queryByText("internet_access_allowed")).toBeNull();
  expect((await opened("Model")).map((option) => option.textContent)).toEqual(NEW_AGENT.models);
  await userEvent.keyboard("{Escape}");
  expect((await opened("Reasoning")).map((option) => option.textContent)).toEqual(
    SPEC_SCHEMA.properties.reasoning.enum,
  );
});

test("a member the kind admits no create from is offered no act", async () => {
  wire({ "/transcript": () => json({ messages: [] }) });
  render(
    <App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />,
  );

  await userEvent.click(screen.getByRole("button", { name: "Agents" }));

  expect(screen.getByRole("button", { name: "Refresh" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "New agent" })).toBeNull();
});

test("the search narrows the rows, and says so when it matches none of them", async () => {
  wire({ "/transcript": () => json({ messages: [] }) });
  render(
    <App
      agents={[AGENT, RESEARCH]}
      subagents={[]}
      member={ADMIN}
      newAgent={NEW_AGENT}
      onAgents={() => {}}
    />,
  );

  await userEvent.click(screen.getByRole("button", { name: "Agents" }));
  await userEvent.type(screen.getByLabelText("Search agents"), "res");

  expect(screen.getByText("research")).toBeTruthy();
  expect(screen.queryByText("assistant")).toBeNull();

  await userEvent.type(screen.getByLabelText("Search agents"), "xx");

  expect(await screen.findByText("No agent matches this search.")).toBeTruthy();
});

test("Refresh re-reads the one answer to what agents this member holds", async () => {
  let reads = 0;
  wire({
    "/api/agents": () => {
      reads += 1;
      return boot(reads > 1 ? [AGENT, RESEARCH] : [AGENT], ADMIN, NEW_AGENT);
    },
    "/transcript": () => json({ messages: [] }),
  });
  render(<Portal />);

  await userEvent.click(await screen.findByRole("button", { name: "Agents" }));
  expect(screen.queryByText("research")).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Refresh" }));

  expect(await screen.findByText("research")).toBeTruthy();
});
