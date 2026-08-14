import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import { AGENT, MEMBER, fact, json, pressRow, useStreamFake, wire } from "./harness";

const RESEARCH = { name: "deep_research", model: "claude-opus-4-8" };
const GENERAL = { name: "general_purpose", model: null };
const RUN_ID = "66666666-6666-4666-8666-666666666666";

const OVERVIEW = {
  subagent: {
    name: "deep_research",
    model: "claude-opus-4-8",
    prompt: "Research the question and cite every source.",
    max_rounds: 40,
    untrusted_output: true,
    loads_skills: true,
  },
};

beforeEach(() => {
  location.hash = "";
  useStreamFake();
});

function tableRow(name: string): HTMLElement {
  const found = screen.getByText(name).closest("tr");
  if (!found) throw new Error("no row named " + name);
  return found;
}

function portal(routes: Record<string, () => Response>) {
  render(<App agents={[AGENT]} subagents={[RESEARCH, GENERAL]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
  return routes;
}

test("a subagent stands in the one table, and the family filter narrows to it", async () => {
  portal({});

  await userEvent.click(screen.getByRole("button", { name: "Agents" }));

  expect(screen.getByText("deep_research")).toBeTruthy();
  expect(screen.getByText("assistant")).toBeTruthy();

  await userEvent.click(screen.getByRole("tab", { name: "Subagents" }));

  expect(screen.getByText("deep_research")).toBeTruthy();
  expect(within(screen.getByRole("table")).queryByText("assistant")).toBeNull();
});

test("the pill marks every row the Subagents filter keeps, and no row it drops", async () => {
  portal({});

  await userEvent.click(screen.getByRole("button", { name: "Agents" }));

  const table = within(screen.getByRole("table"));
  expect(table.getAllByRole("button", { name: "Subagent" }).length).toBe(2);
  expect(within(tableRow("assistant")).queryByRole("button", { name: "Subagent" })).toBeNull();

  await userEvent.click(screen.getByRole("tab", { name: "Subagents" }));

  expect(table.getAllByRole("button", { name: "Subagent" }).length).toBe(2);
});

test("the pill says what a subagent is on keyboard focus and on a press, never on hover alone", async () => {
  portal({});

  await userEvent.click(screen.getByRole("button", { name: "Agents" }));
  const pill = within(tableRow("deep_research")).getByRole("button", { name: "Subagent" });

  expect(screen.queryByRole("tooltip")).toBeNull();
  fireEvent.focus(pill);

  expect((await screen.findByRole("tooltip")).textContent).toBe(
    "An agent starts one to do a single task and report back.",
  );

  fireEvent.blur(pill);
  await waitFor(() => expect(screen.queryByRole("tooltip")).toBeNull());

  await userEvent.click(pill);

  expect(await screen.findByRole("tooltip")).toBeTruthy();
  expect(location.hash).toBe("#/agents");

  await userEvent.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("tooltip")).toBeNull());
});

test("a deploy declaring no subagent says so under the family that has none", async () => {
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await userEvent.click(screen.getByRole("button", { name: "Agents" }));
  await userEvent.click(screen.getByRole("tab", { name: "Subagents" }));

  expect(screen.getByText("This deploy declares no subagents.")).toBeTruthy();
  expect(screen.getByRole("table")).toBeTruthy();
});

test("a subagent row opens its page, naming the model, round limit, and untrusted wall", async () => {
  wire({ "/subagents/deep_research/overview": () => json(OVERVIEW) });
  portal({});

  await userEvent.click(screen.getByRole("button", { name: "Agents" }));
  await pressRow("deep_research");

  expect(location.hash).toBe("#/subagents/deep_research");
  const panel = within(await screen.findByTestId("panel"));
  expect(fact("Model")).toBe("claude-opus-4-8");
  expect(fact("Round limit")).toBe("40");
  expect(fact("Answer to parent")).toBe("Walled as untrusted content");
  expect(panel.getByText("Research the question and cite every source.")).toBeTruthy();
});

test("a subagent inheriting its parent's model says so rather than naming one", async () => {
  wire({
    "/subagents/general_purpose/overview": () =>
      json({
        subagent: {
          ...OVERVIEW.subagent,
          name: "general_purpose",
          model: null,
          untrusted_output: false,
          loads_skills: false,
        },
      }),
  });
  portal({});

  await userEvent.click(screen.getByRole("button", { name: "Agents" }));
  await pressRow("general_purpose");

  await screen.findByTestId("panel");
  expect(fact("Model")).toBe("Inherits the agent that spawns it");
  expect(fact("Answer to parent")).toBe("Trusted content");
});

const RUN = {
  id: RUN_ID,
  agent: { id: AGENT.id, name: "assistant" },
  surface: "subagent",
  surface_label: null,
  audience: "member:m1",
  member_email: "member@example.com",
  description: "Find the filing deadline",
  speakers: [],
  turn_count: 1,
  created_at: "2026-08-02T09:00:00Z",
  last_turn_at: "2026-08-02T09:05:00Z",
  readable: true,
  disclosable: false,
};

const WALLED_RUN = {
  ...RUN,
  id: "88888888-8888-4888-8888-888888888888",
  agent: { id: "99999999-9999-4999-8999-999999999999", name: "ops" },
  member_email: null,
  audience: "room:slack:C9",
  description: "",
  turn_count: 3,
  last_turn_at: "2026-08-01T09:30:00Z",
  readable: false,
};

const NESTED_ID = "55555555-5555-4555-8555-555555555555";

const RUN_TRANSCRIPT = {
  run: RUN,
  messages: [
    { role: "user", text: "Find the filing deadline" },
    {
      role: "assistant",
      text: "March 31",
      events: [{ kind: "tool", name: "fetch_url", preview: "", description: "Reading the filing" }],
      subagents: [
        {
          profile: "general_purpose",
          conversation_id: NESTED_ID,
          events: [{ kind: "note", text: "Checking the state site." }],
          output: "The state confirms March 31.",
          subagents: [],
        },
      ],
    },
  ],
};

test("the conversations tab lists this subagent's runs as rows that name the agent", async () => {
  wire({
    ["/subagents/deep_research/conversations/" + RUN_ID]: () => json(RUN_TRANSCRIPT),
    "/subagents/deep_research/conversations": () => json({ conversations: [RUN, WALLED_RUN] }),
    "/subagents/deep_research/overview": () => json(OVERVIEW),
  });
  location.hash = "#/subagents/deep_research/conversations";
  portal({});

  const panel = within(await screen.findByTestId("panel"));
  const row = await panel.findByRole("button", { name: /Find the filing deadline/ });
  expect(row.querySelector("[data-part='primary']")!.textContent).toBe("Find the filing deadline");
  expect(row.querySelector("[data-part='meta']")!.textContent).toBe("You · assistant · 1 turn");
  expect(row.querySelector("[data-part='when']")!.textContent).toBe("Aug 2 2026");

  const walled = panel
    .getByText("Private channel", { selector: "[data-part='primary']" })
    .closest("li")!;
  expect(walled.querySelector("[data-part='meta']")!.textContent).toBe("ops · 3 turns");
  expect(walled.getAttribute("role")).toBeNull();

  await userEvent.click(row);
  expect(location.hash).toBe("#/subagents/deep_research/conversations/" + RUN_ID);
  expect(await screen.findByText("Find the filing deadline")).toBeTruthy();
  expect(screen.getByText("March 31")).toBeTruthy();
  expect(screen.queryByRole("link", { name: /Changes/ })).toBeNull();
});

test("a search over the runs states that nothing matched", async () => {
  wire({
    "/subagents/deep_research/conversations": () => json({ conversations: [RUN] }),
    "/subagents/deep_research/overview": () => json(OVERVIEW),
  });
  location.hash = "#/subagents/deep_research/conversations";
  portal({});

  const panel = within(await screen.findByTestId("panel"));
  await userEvent.type(await panel.findByLabelText("Search"), "ops");

  expect(panel.getByText("No conversation matches this search.")).toBeTruthy();
  expect(panel.queryByText("Find the filing deadline")).toBeNull();
});

test("a run permalink opened cold titles the page without reading the listing", async () => {
  const { calls } = wire({
    ["/subagents/deep_research/conversations/" + RUN_ID]: () => json(RUN_TRANSCRIPT),
    "/subagents/deep_research/overview": () => json(OVERVIEW),
  });
  location.hash = "#/subagents/deep_research/conversations/" + RUN_ID;
  portal({});

  expect(await screen.findByText("assistant · Find the filing deadline")).toBeTruthy();
  expect(screen.getByText("March 31")).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Conversations" }).getAttribute("aria-selected")).toBe(
    "true",
  );
  expect(screen.getByRole("link", { name: "All conversations" }).getAttribute("href")).toBe(
    "#/subagents/deep_research/conversations",
  );
  expect(calls.filter((url) => url.endsWith("/conversations"))).toEqual([]);
});

test("a run's own page nests the work of the runs it spawned, each rooted at it", async () => {
  wire({
    ["/subagents/deep_research/conversations/" + RUN_ID]: () => json(RUN_TRANSCRIPT),
    "/subagents/deep_research/overview": () => json(OVERVIEW),
  });
  location.hash = "#/subagents/deep_research/conversations/" + RUN_ID;
  portal({});

  const summary = await screen.findByText("Checking the state site.");
  expect(summary.tagName).toBe("SUMMARY");
  expect(screen.queryByText("Reading the filing")).toBeNull();

  await userEvent.click(summary);
  expect(screen.getByText("Reading the filing")).toBeTruthy();
  expect(screen.getByText("The state confirms March 31.")).toBeTruthy();
  expect(
    screen.getByRole("link", { name: /Subagent · general_purpose/ }).getAttribute("href"),
  ).toBe("#/subagents/general_purpose/conversations/" + NESTED_ID + "?root=" + RUN_ID);
});

test("a run opened from the conversation that spawned it reads through that conversation", async () => {
  const parent = "44444444-4444-4444-8444-444444444444";
  const { calls } = wire({
    ["/subagents/deep_research/conversations/" + RUN_ID]: () => json(RUN_TRANSCRIPT),
    "/subagents/deep_research/overview": () => json(OVERVIEW),
  });
  location.hash = "#/subagents/deep_research/conversations/" + RUN_ID + "?root=" + parent;
  portal({});

  expect(await screen.findByText("March 31")).toBeTruthy();
  expect(calls.some((url) => url.endsWith("/conversations/" + RUN_ID + "?root=" + parent))).toBe(
    true,
  );
});

test("a run the viewer may not read refuses the permalink instead of titling a page", async () => {
  wire({
    ["/subagents/deep_research/conversations/" + RUN_ID]: () =>
      new Response("no such conversation", { status: 404 }),
    "/subagents/deep_research/overview": () => json(OVERVIEW),
  });
  location.hash = "#/subagents/deep_research/conversations/" + RUN_ID;
  portal({});

  expect(await screen.findByText("This conversation is not shared with you.")).toBeTruthy();
});

test("a subagent holding no load_skill says it loads none instead of listing skills", async () => {
  wire({
    "/subagents/general_purpose/skills": () => json({ loads_skills: false, skills: [] }),
    "/subagents/general_purpose/overview": () => json(OVERVIEW),
  });
  location.hash = "#/subagents/general_purpose/skills";
  portal({});

  expect(
    await screen.findByText("This subagent holds no load_skill tool, so it loads no skills."),
  ).toBeTruthy();
});

test("the skills tab lists the deploy skills a subagent can load", async () => {
  wire({
    "/subagents/deep_research/skills": () =>
      json({
        loads_skills: true,
        skills: [{ name: "office/docx", description: "Write a Word document." }],
      }),
    "/subagents/deep_research/overview": () => json(OVERVIEW),
  });
  location.hash = "#/subagents/deep_research/skills";
  portal({});

  const panel = within(await screen.findByTestId("panel"));
  expect(await panel.findByText("office/docx")).toBeTruthy();
  expect(panel.getByText("Write a Word document.")).toBeTruthy();
});

test("a hash naming no registered subagent renders no page", async () => {
  wire({});
  location.hash = "#/subagents/nonexistent";
  portal({});

  expect(await screen.findByText("No such subagent.")).toBeTruthy();
});
