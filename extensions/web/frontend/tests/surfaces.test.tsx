import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { ALL_SURFACES } from "@/lib/surfaces";
import type { Surfaces } from "@/lib/types";

import { AGENT, atPhoneWidth, json, MEMBER, SECOND, useStreamFake, wire } from "./harness";

/** The workspace column, which the shell draws at a phone width alone: the drawer the hamburger
 *  opens holds it, so a test reading the rows or the foot opens it first. */
async function openWorkspaceColumn(): Promise<HTMLElement> {
  await userEvent.click(await screen.findByRole("button", { name: "Menu" }));
  const drawer = await screen.findByRole("dialog");
  return within(drawer).getByRole("navigation", { name: "Workspace" });
}

beforeEach(() => {
  useStreamFake();
  location.hash = "";
  wire({
    "/api/members": () => json({ members: [], invitations: [] }),
    "/objects/": () => json({ objects: [] }),
    "/transcript": () => json({ messages: [] }),
  });
});

const ADMIN = { ...MEMBER, admin: true };
const WITHHELD: Surfaces = {
  admin: false,
  memory: false,
  "community-skills": false,
  "installed-skills": false,
  usage: false,
};

/** An app the deploy withholds. The mark rides the boot read's own agent row, so the app is the
 *  workspace's either way — this is what the portal draws it out of, not what it may open. */
const HIDDEN_APP = { ...SECOND, name: "wiki", app: "wiki", hidden: true };

test("an app the deploy offers is a row the sidebar pins itself", async () => {
  atPhoneWidth();
  render(
    <App
      agents={[AGENT, { ...HIDDEN_APP, hidden: false }]}
      member={MEMBER}
      surfaces={ALL_SURFACES}
      onAgents={() => {}}
    />,
  );

  const rail = await openWorkspaceColumn();
  expect(within(rail).getByRole("button", { name: "Wiki" })).toBeTruthy();
});

test("an app the deploy withholds is no such row", async () => {
  atPhoneWidth();
  render(
    <App agents={[AGENT, HIDDEN_APP]} member={MEMBER} surfaces={ALL_SURFACES} onAgents={() => {}} />,
  );

  const rail = await openWorkspaceColumn();
  expect(within(rail).queryByRole("button", { name: "Wiki" })).toBeNull();
});

test("a withheld app still opens from its own address", async () => {
  location.hash = "#/agents/" + HIDDEN_APP.id;
  render(
    <App agents={[AGENT, HIDDEN_APP]} member={MEMBER} surfaces={ALL_SURFACES} onAgents={() => {}} />,
  );

  expect(await screen.findByRole("region", { name: "Wiki" })).toBeTruthy();
});

test("a withheld workspace screen loses its tab", async () => {
  location.hash = "#/workspace/team";
  render(<App agents={[AGENT]} member={MEMBER} surfaces={WITHHELD} onAgents={() => {}} />);

  await waitFor(() => expect(screen.queryByRole("tab", { name: "Team" })).toBeTruthy());
  expect(screen.queryByRole("tab", { name: "Memory" })).toBeNull();
  expect(screen.queryByRole("tab", { name: "Skills" })).toBeNull();
  expect(screen.queryByRole("tab", { name: "Usage" })).toBeNull();
});

test("the skills tab stands while either of its two panels is offered", async () => {
  location.hash = "#/workspace/team";
  render(
    <App
      agents={[AGENT]}
      member={MEMBER}
      surfaces={{ ...WITHHELD, "installed-skills": true }}
      onAgents={() => {}}
    />,
  );

  expect(await screen.findByRole("tab", { name: "Skills" })).toBeTruthy();
  expect(screen.queryByRole("tab", { name: "Memory" })).toBeNull();
});

test("an offered workspace screen keeps its tab", async () => {
  location.hash = "#/workspace/team";
  render(<App agents={[AGENT]} member={MEMBER} surfaces={ALL_SURFACES} onAgents={() => {}} />);

  expect(await screen.findByRole("tab", { name: "Memory" })).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Usage" })).toBeTruthy();
  expect(screen.queryByRole("tab", { name: "Connectors" })).toBeNull();
  expect(screen.queryByRole("tab", { name: "Sources" })).toBeNull();
});

test("the administration gear goes where the deploy withholds it, admin or not", async () => {
  atPhoneWidth();
  const withheld = render(
    <App agents={[AGENT]} member={ADMIN} surfaces={WITHHELD} onAgents={() => {}} />,
  );
  const foot = within(await openWorkspaceColumn());
  expect(foot.getByRole("button", { name: "Sign out" })).toBeTruthy();
  expect(foot.queryByRole("button", { name: "Administration" })).toBeNull();
  withheld.unmount();

  render(<App agents={[AGENT]} member={ADMIN} surfaces={ALL_SURFACES} onAgents={() => {}} />);
  expect(
    within(await openWorkspaceColumn()).getByRole("button", { name: "Administration" }),
  ).toBeTruthy();
});
