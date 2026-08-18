import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { Portal } from "@/Portal";
import { AGENT_ICONS } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";

import {
  AGENT,
  AGENT_ID,
  MEMBER,
  SECOND_ID,
  SETTINGS,
  agentIndex,
  json,
  opened,
  openAgentRow,
  openAgentSettings,
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
const RESEARCH = {
  id: SECOND_ID,
  name: "research",
  model: "claude-opus-4-8",
  main: false,
  icon: "telescope",
};
/** An app whose name a member typed as two lowercase words, which is the case the drawn name and
 *  the stored name differ in most plainly. */
const REVIEWER = {
  id: SECOND_ID,
  name: "code reviewer",
  model: "claude-opus-4-8",
  main: false,
  icon: "code",
};
const PROMPT = "Answer with sources.";

function boot(agents: unknown[], member: unknown, newAgent: unknown) {
  return json({ member, agents, new_agent: newAgent });
}

async function openCreate() {
  await userEvent.click(await screen.findByRole("button", { name: "Apps" }));
  await userEvent.click(await screen.findByRole("button", { name: "New application" }));
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
    create_only: true,
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
  expect(await screen.findByText("Created Research.")).toBeTruthy();

  await openAgentRow("Research");

  expect(location.hash).toBe("#/agents/" + SECOND_ID);
  expect(screen.queryByText("No such agent.")).toBeNull();
  expect(await screen.findByRole("region", { name: "Research" })).toBeTruthy();
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
  expect(screen.queryByText("Created Research.")).toBeNull();
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

  expect(document.querySelectorAll("h1").length).toBe(0);
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
    <App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />,
  );

  await userEvent.click(screen.getByRole("button", { name: "Apps" }));

  expect(await agentIndex()).toBeTruthy();
  expect(screen.queryByRole("button", { name: "New application" })).toBeNull();
});

test("each row in the index draws its own app's mark, and states nothing by it", async () => {
  wire({ "/transcript": () => json({ messages: [] }) });
  render(<App agents={[AGENT, RESEARCH]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await userEvent.click(screen.getByRole("button", { name: "Apps" }));
  const index = within(await agentIndex());

  const assistant = index.getByRole("button", { name: /^Assistant/ });
  expect(assistant.querySelector(".tabler-icon-robot")).toBeTruthy();
  expect(assistant.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
  expect(index.getByRole("button", { name: /^Research/ }).querySelector(".tabler-icon-telescope"))
    .toBeTruthy();
});

test("a member picks another mark, and the pick rides one intent and comes back", async () => {
  const posted: unknown[] = [];
  let icon = "robot";
  wire({
    "/settings": () => json({ ...SETTINGS, spec: { ...SETTINGS.spec, icon } }),
    "/connections": () => json({ connections: [] }),
    "/intents": (_url, init) => {
      const body = JSON.parse(String(init?.body));
      posted.push(body);
      icon = String(body.spec.icon);
      return json({ applied: true, message: "Applied." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
  await openAgentSettings();

  const marks = await screen.findAllByRole("radio");
  expect(marks.length).toBe(Object.keys(AGENT_ICONS).length);
  // The set leads with the workspace's own mark, and the picker draws the set in its own order.
  expect(Object.keys(AGENT_ICONS)[0]).toBe("ufo");
  expect(marks[0].getAttribute("value")).toBe("ufo");
  // Every label is the slug's words capitalized, except the product's own name, which is read as
  // it is written.
  expect(marks[0].getAttribute("aria-label")).toBe("ufo");
  expect(screen.getByRole("radio", { name: "Shopping cart" })).toBeTruthy();
  expect((screen.getByRole("radio", { name: "Robot" }) as HTMLInputElement).checked).toBe(true);
  // The schema hides `icon` the way it hides `prompt`, so the generic form draws no control for it.
  expect(screen.queryByRole("combobox", { name: "icon" })).toBeNull();
  expect(screen.queryByRole("textbox", { name: "icon" })).toBeNull();

  await userEvent.click(screen.getByRole("radio", { name: "Chart line" }));
  await userEvent.click(screen.getByRole("button", { name: "Save icon" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toEqual({
    verb: "apply",
    kind: "agent",
    name: "assistant",
    spec: { icon: "chart-line" },
  });
  expect(await screen.findByText("Applied.")).toBeTruthy();
  await waitFor(() =>
    expect((screen.getByRole("radio", { name: "Chart line" }) as HTMLInputElement).checked).toBe(
      true,
    ),
  );
  expect((screen.getByRole("radio", { name: "Robot" }) as HTMLInputElement).checked).toBe(false);
});

test("a name is drawn word by word, and only a word written wholly in lowercase is raised", () => {
  expect(agentName("assistant")).toBe("Assistant");
  expect(agentName("code reviewer")).toBe("Code Reviewer");
  expect(agentName("Code reviewer")).toBe("Code Reviewer");
  expect(agentName("iOS helper")).toBe("iOS Helper");
  // A hyphen parts words the way a space does, so an extension's slug is not left half-drawn.
  expect(agentName("daily-brief")).toBe("Daily-Brief");
  expect(agentName("release_bot")).toBe("Release_Bot");
});

test("the index row and the pane header draw the app's name in Title Case", async () => {
  wire({ "/transcript": () => json({ messages: [] }) });
  location.hash = "#/agents/" + SECOND_ID;
  render(<App agents={[AGENT, REVIEWER]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  const index = within(await agentIndex());
  expect(index.getByRole("button", { name: /^Code Reviewer/ })).toBeTruthy();
  expect(index.queryByText("code reviewer")).toBeNull();

  const pane = await screen.findByRole("region", { name: "Code Reviewer" });
  expect(within(pane).getByRole("heading", { name: "Code Reviewer" })).toBeTruthy();
});

/** The drawn name is text; the stored name is the app's identity, and it is what addresses the
 *  object in the intent. A screen that raised the name it writes would rename the app on its first
 *  save. */
test("the settings dialog reads the drawn name and the intent it posts carries the stored one", async () => {
  const posted: { name: string }[] = [];
  wire({
    "/settings": () => json({ ...SETTINGS, agent: { ...SETTINGS.agent, name: REVIEWER.name } }),
    "/connections": () => json({ connections: [] }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Applied." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + SECOND_ID;
  render(<App agents={[AGENT, REVIEWER]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
  const dialog = within(await openAgentSettings("Code Reviewer"));

  expect(dialog.getByRole("heading", { name: "Code Reviewer" })).toBeTruthy();

  await userEvent.click(dialog.getByRole("button", { name: "Save prompt" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0].name).toBe("code reviewer");
});
