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
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
}

test("a set homepage frames the bound site beside the conversation", async () => {
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

  const frame = await screen.findByTitle("Assistant homepage");
  expect(frame.tagName).toBe("IFRAME");
  expect(frame.getAttribute("src")).toBe(HOMEPAGE_URL);
  expect(frame.hasAttribute("sandbox")).toBe(false);
  // The one act on the half, and the one that leaves the portal.
  const out = screen.getByRole("link", { name: "Open Assistant homepage" });
  expect(out.getAttribute("href")).toBe(HOMEPAGE_URL);
  expect(out.getAttribute("target")).toBe("_blank");
});

test("a homepage being built draws the page's shape rather than the page", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  open({ "/homepage": () => json({ state: "building" }) });

  const half = await screen.findByRole("region", { name: "Assistant homepage" });
  expect(half.getAttribute("aria-busy")).toBe("true");
  expect(half.querySelector("iframe")).toBeNull();
  expect(half.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0);
  // Nothing to open until the build settles.
  expect(screen.queryByRole("link", { name: "Open Assistant homepage" })).toBeNull();
});

test("an app with no homepage draws one column and no second half", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  open({ "/homepage": () => json({ state: "none" }) });

  expect(await screen.findByRole("region", { name: "Assistant" })).toBeTruthy();
  expect(screen.queryByRole("region", { name: "Assistant homepage" })).toBeNull();
  expect(document.querySelector("iframe")).toBeNull();
});

test("the bare agents hash shows the main agent without navigating", async () => {
  location.hash = "#/agents";
  open({});

  expect(await screen.findByRole("region", { name: "Assistant" })).toBeTruthy();
  expect(location.hash).toBe("#/agents");
});
