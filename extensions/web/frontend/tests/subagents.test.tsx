import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import { AGENT, MEMBER, json, useStreamFake, wire } from "./harness";

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

function portal(routes: Record<string, () => Response>) {
  render(<App agents={[AGENT]} subagents={[RESEARCH, GENERAL]} member={MEMBER} />);
  return routes;
}

test("a subagent row opens its page, naming the model, round limit, and untrusted wall", async () => {
  wire({ "/subagents/deep_research/overview": () => json(OVERVIEW) });
  portal({});

  await userEvent.click(screen.getByRole("button", { name: "Agents" }));
  await userEvent.click(screen.getByRole("button", { name: "deep_research · subagent" }));

  expect(location.hash).toBe("#/subagents/deep_research");
  const panel = within(await screen.findByTestId("panel"));
  expect(panel.getByText(/model: claude-opus-4-8/)).toBeTruthy();
  expect(panel.getByText(/round limit: 40/)).toBeTruthy();
  expect(panel.getByText(/walled as untrusted content/)).toBeTruthy();
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
  await userEvent.click(screen.getByRole("button", { name: "general_purpose · subagent" }));

  const panel = within(await screen.findByTestId("panel"));
  expect(panel.getByText(/model: inherits the agent that spawns it/)).toBeTruthy();
  expect(panel.getByText(/reaches a parent as trusted/)).toBeTruthy();
});

const RUN_DETAIL = {
  run: {
    id: RUN_ID,
    agent_name: "assistant",
    member_email: "member@example.com",
    turn_count: 1,
    last_turn_at: "2026-08-02T09:05:00Z",
    readable: true,
  },
};

test("the conversations tab lists this subagent's runs and opens one as a turn tree", async () => {
  wire({
    ["/subagents/deep_research/conversations/" + RUN_ID]: () =>
      json({
        ...RUN_DETAIL,
        turns: [
          {
            id: "77777777-7777-4777-8777-777777777777",
            agent_id: AGENT.id,
            conversation_id: RUN_ID,
            seq: 2,
            status: "done",
            created_at: "2026-08-02T09:00:00Z",
            inbound: "Find the filing deadline",
            outcome: "March 31",
            error_class: null,
            subagent_profile: "deep_research",
            parent_turn_id: null,
          },
        ],
        subagent_turns: [],
      }),
    "/subagents/deep_research/conversations": () =>
      json({
        conversations: [
          {
            id: RUN_ID,
            agent_name: "assistant",
            member_email: "member@example.com",
            turn_count: 1,
            last_turn_at: "2026-08-02T09:05:00Z",
            readable: true,
          },
          {
            id: "88888888-8888-4888-8888-888888888888",
            agent_name: "ops",
            member_email: null,
            turn_count: 3,
            last_turn_at: "2026-08-01T09:30:00Z",
            readable: false,
          },
        ],
      }),
    "/subagents/deep_research/overview": () => json(OVERVIEW),
  });
  location.hash = "#/subagents/deep_research/conversations";
  portal({});

  const panel = within(await screen.findByTestId("panel"));
  expect(await panel.findByText("member@example.com")).toBeTruthy();
  const rows = panel.getAllByRole("row").slice(1);
  expect(rows.map((row) => within(row).getAllByRole("cell").map((cell) => cell.textContent))).toEqual([
    ["assistant", "member@example.com", "1", "2026-08-02 09:05", "Open"],
    ["ops", "Channel or room · 88888888", "3", "2026-08-01 09:30", "not shared with you"],
  ]);

  await userEvent.click(panel.getByRole("link", { name: "Open" }));
  expect(location.hash).toBe("#/subagents/deep_research/conversations/" + RUN_ID);
  expect(await screen.findByText("Find the filing deadline")).toBeTruthy();
  expect(screen.getByText("March 31")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Changes" }).getAttribute("href")).toBe(
    "#/agents/" + AGENT.id + "/conversations/" + RUN_ID + "/changes",
  );
});

test("a run permalink opened cold titles the page without reading the listing", async () => {
  const { calls } = wire({
    ["/subagents/deep_research/conversations/" + RUN_ID]: () =>
      json({
        ...RUN_DETAIL,
        turns: [
          {
            id: "77777777-7777-4777-8777-777777777777",
            agent_id: AGENT.id,
            conversation_id: RUN_ID,
            seq: 2,
            status: "done",
            created_at: "2026-08-02T09:00:00Z",
            inbound: "Find the filing deadline",
            outcome: "March 31",
            error_class: null,
            subagent_profile: "deep_research",
            parent_turn_id: null,
          },
        ],
        subagent_turns: [],
      }),
    "/subagents/deep_research/overview": () => json(OVERVIEW),
  });
  location.hash = "#/subagents/deep_research/conversations/" + RUN_ID;
  portal({});

  expect(await screen.findByText("assistant · member@example.com")).toBeTruthy();
  expect(screen.getByText("Find the filing deadline")).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Conversations" }).getAttribute("aria-selected")).toBe(
    "true",
  );
  expect(screen.getByRole("link", { name: "All conversations" }).getAttribute("href")).toBe(
    "#/subagents/deep_research/conversations",
  );
  expect(calls.filter((url) => url.endsWith("/conversations"))).toEqual([]);
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
