import { render, screen } from "@testing-library/react";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import { AGENT, AGENT_ID, MEMBER, json, useStreamFake, wire } from "./harness";

const HOMEPAGE_URL = "/surface/sites/tok-abc/";

beforeEach(() => {
  location.hash = "";
  useStreamFake();
});

function open(routes: Parameters<typeof wire>[0]) {
  wire({ "/transcript": () => json({ messages: [] }), ...routes });
  render(
    <App agents={[AGENT]} member={MEMBER} newAgent={null} onAgents={() => {}} />,
  );
}

test("a set homepage frames the bound site in the pane", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  open({
    "/homepage": () =>
      json({
        state: "set",
        url: HOMEPAGE_URL,
        visibility: "workspace",
        updated_at: "2026-08-16T00:00:00Z",
      }),
  });

  const frame = await screen.findByTitle("assistant homepage");
  expect(frame.tagName).toBe("IFRAME");
  expect(frame.getAttribute("src")).toBe(HOMEPAGE_URL);
  expect(frame.hasAttribute("sandbox")).toBe(false);
});

test("an absent homepage states the one line and nothing else", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  open({ "/homepage": () => json({ state: "none" }) });

  expect(await screen.findByText("assistant has not built its homepage.")).toBeTruthy();
  expect(document.querySelector("iframe")).toBeNull();
});

test("the bare agents hash shows the main agent's Home without navigating", async () => {
  location.hash = "#/agents";
  open({});

  expect(await screen.findByText("assistant has not built its homepage.")).toBeTruthy();
  expect(location.hash).toBe("#/agents");
});
