import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { parseHash } from "@/lib/route";
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
  team: false,
  memory: false,
  "community-skills": false,
  "installed-skills": false,
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
  location.hash = "#/workspace/apps";
  render(<App agents={[AGENT]} member={MEMBER} surfaces={WITHHELD} onAgents={() => {}} />);

  await waitFor(() => expect(screen.queryByRole("tab", { name: "Apps" })).toBeTruthy());
  expect(screen.queryByRole("tab", { name: "Team" })).toBeNull();
  expect(screen.queryByRole("tab", { name: "Memory" })).toBeNull();
  expect(screen.queryByRole("tab", { name: "Skills" })).toBeNull();
  expect(screen.getByRole("tab", { name: "Usage" })).toBeTruthy();
});

test("the skills tab stands while either of its two panels is offered", async () => {
  location.hash = "#/workspace/apps";
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
  expect(screen.getByRole("tab", { name: "Connectors" })).toBeTruthy();
  expect(screen.queryByRole("tab", { name: "Sources" })).toBeNull();
});

/** The roster is an admin's screen, so the tab that opens it is drawn for an admin alone. Its
 *  address still answers everyone — a withheld screen loses its tab and keeps its address — and
 *  the destination opens on the first tab this member is drawn, so no entry to it lands a non-admin
 *  on a tab that is not there. */
test("the team tab is an admin's, and the workspace opens on the first tab drawn", async () => {
  location.hash = "#/workspace/apps";
  const plain = render(
    <App
      agents={[AGENT]}
      member={MEMBER}
      surfaces={{ ...ALL_SURFACES, team: false }}
      onAgents={() => {}}
    />,
  );
  expect(await screen.findByRole("tab", { name: "Apps" })).toBeTruthy();
  expect(screen.queryByRole("tab", { name: "Team" })).toBeNull();
  plain.unmount();

  render(<App agents={[AGENT]} member={ADMIN} surfaces={ALL_SURFACES} onAgents={() => {}} />);
  expect(await screen.findByRole("tab", { name: "Team" })).toBeTruthy();
});

/** The rail row and the palette row both open the destination's first drawn tab, so a member the
 *  roster is not drawn for lands on Apps rather than on a tab the strip does not carry. */
test("the workspace row lands a member on the first tab they are drawn", async () => {
  location.hash = "";
  const desk = render(
    <App
      agents={[AGENT]}
      member={MEMBER}
      surfaces={{ ...ALL_SURFACES, team: false }}
      onAgents={() => {}}
    />,
  );

  const rail = within(await screen.findByRole("navigation", { name: "Tabs" }));
  await userEvent.click(rail.getByRole("button", { name: "Workspace" }));

  expect(parseHash(location.hash)).toEqual({ kind: "workspace", view: "apps", place: {} });
  expect(await screen.findByRole("tab", { name: "Apps" })).toBeTruthy();
  desk.unmount();

  location.hash = "";
  atPhoneWidth();
  render(
    <App
      agents={[AGENT]}
      member={MEMBER}
      surfaces={{ ...ALL_SURFACES, team: false }}
      onAgents={() => {}}
    />,
  );

  const drawer = within(await openWorkspaceColumn());
  await userEvent.click(drawer.getByRole("button", { name: "Workspace" }));

  expect(parseHash(location.hash)).toEqual({ kind: "workspace", view: "apps", place: {} });
});

/** The palette reaches the same destinations the nav does and lists each of them once. Here the
 *  workspace opens on Apps, whose address this shell already carries above, so the palette states
 *  that one place once rather than under two names. */
test("the palette carries no second name for the workspace", async () => {
  location.hash = "";
  render(
    <App
      agents={[AGENT]}
      member={MEMBER}
      surfaces={{ ...ALL_SURFACES, team: false }}
      onAgents={() => {}}
    />,
  );

  await userEvent.click(await screen.findByRole("button", { name: "Launcher" }));

  expect(await screen.findByRole("option", { name: "Apps" })).toBeTruthy();
  expect(screen.queryByRole("option", { name: "Workspace" })).toBeNull();

  await userEvent.click(screen.getByRole("option", { name: "Apps" }));
  expect(parseHash(location.hash)).toEqual({ kind: "workspace", view: "apps", place: {} });
});
