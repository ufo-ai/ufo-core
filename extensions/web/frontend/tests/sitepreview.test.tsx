import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import { AGENT, AGENT_ID, MEMBER, json, useStreamFake, wire } from "./harness";

const SITE_URL = "/surface/sites/tok-abc/";

beforeEach(() => {
  location.hash = "";
  useStreamFake();
});

function open(routes: Parameters<typeof wire>[0] = {}) {
  wire({ "/transcript": () => json({ messages: [] }), ...routes });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
}

test("a set homepage stands live in the sidebar, scaled down and inert", async () => {
  open({ "/homepage": () => json({ state: "set", url: SITE_URL }) });

  const nav = await screen.findByRole("navigation", { name: "Workspace" });
  const frame = await within(nav).findByTitle("Assistant homepage preview");
  expect(frame.tagName).toBe("IFRAME");
  expect(frame.getAttribute("src")).toBe(SITE_URL);
  expect(frame.hasAttribute("sandbox")).toBe(false);
  expect(frame.className).toContain("pointer-events-none");
  expect(frame.getAttribute("tabindex")).toBe("-1");
  expect(frame.style.transform).toContain("scale");
});

test("the preview card opens the app screen where the site stands whole", async () => {
  open({ "/homepage": () => json({ state: "set", url: SITE_URL }) });

  const nav = await screen.findByRole("navigation", { name: "Workspace" });
  await userEvent.click(await within(nav).findByRole("button", { name: "Assistant homepage" }));
  expect(location.hash).toBe("#/agents/" + AGENT_ID);
});

test("no homepage leaves the sidebar without a frame", async () => {
  open();

  const nav = await screen.findByRole("navigation", { name: "Workspace" });
  await within(nav).findByRole("button", { name: /New conversation/ });
  expect(within(nav).queryByTitle("Assistant homepage preview")).toBeNull();
  expect(nav.querySelector("iframe")).toBeNull();
});
