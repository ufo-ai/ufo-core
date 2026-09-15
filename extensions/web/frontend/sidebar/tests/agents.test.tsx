import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { Portal } from "@/Portal";
import { agentName } from "@/lib/agentName";

import {
  AGENT,
  CONVO_ID,
  MEMBER,
  SECOND_ID,
  StreamFake,
  TURN_ID,
  atPhoneWidth,
  json,
  openNewApplication,
  useStreamFake,
  wire,
} from "../../tests/harness";

const ADMIN = { ...MEMBER, admin: true };
const RESEARCH = {
  id: SECOND_ID,
  name: "research",
  model: "claude-opus-4-8",
  main: false,
  icon: "aten",
};

const TITLE = "Finances dash";

const NO_BOARD = () =>
  json({ type: "tasks", title: "", tasks: [], total_count: 0, completed_count: 0, truncated: false });

const SERVED = ["auto", "claude-opus-4-8", "claude-sonnet-5"];

function boot(agents: unknown[], member: unknown) {
  return json({ member, agents, models: SERVED });
}

const OPENED = { turn_id: TURN_ID, conversation_id: CONVO_ID, title: TITLE };

async function openWizard() {
  await openNewApplication();
  return screen.findByRole("region", { name: "App Creator" });
}

beforeEach(() => {
  location.hash = "";
  useStreamFake();
});

test("the drawer holds the sidebar at a phone width, and a pick shuts it", async () => {
  atPhoneWidth();
  location.hash = "#/agents";
  wire({ "/api/agents": () => boot([AGENT, RESEARCH], ADMIN) });
  render(<Portal />);

  expect(await screen.findByRole("region", { name: "Assistant" })).toBeTruthy();
  expect(screen.queryByRole("navigation", { name: "Workspace" })).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Menu" }));
  const drawer = await screen.findByRole("dialog");
  const sidebar = within(drawer).getByRole("navigation", { name: "Workspace" });
  await userEvent.click(within(sidebar).getByRole("button", { name: "Connections" }));

  expect(location.hash).toBe("#/connectors");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

test("closing the wizard gives the pane back to the app", async () => {
  wire({
    "/api/agents": () => boot([AGENT], ADMIN),
    "/chat": () => json(OPENED),
    "/slots/tasks": NO_BOARD,
    "/transcript": () => json({ messages: [] }),
    "/homepage": () => json({ state: "none" }),
  });
  render(<Portal />);

  await openWizard();
  await userEvent.click(screen.getByRole("button", { name: "Close" }));

  await waitFor(() => expect(screen.queryByRole("region", { name: "App Creator" })).toBeNull());
  expect(await screen.findByRole("region", { name: "Assistant" })).toBeTruthy();
});

test("the wizard's bare address founds nothing and forwards to the apps screen", async () => {
  const sent: string[] = [];
  wire({
    "/api/agents": () => boot([AGENT], MEMBER),
    "/chat": (url) => {
      sent.push(url);
      return json(OPENED);
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/builder";
  render(<Portal />);

  await screen.findByRole("region", { name: agentName(AGENT.name) });
  await waitFor(() => expect(location.hash).toBe("#/agents"));
  expect(screen.queryByRole("region", { name: "App Creator" })).toBeNull();
  expect(StreamFake.opened.length).toBe(0);
  expect(sent).toEqual([]);
});
