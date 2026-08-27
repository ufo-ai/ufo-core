import { render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { ALL_SURFACES } from "@/lib/surfaces";
import type { Surfaces } from "@/lib/types";

import { AGENT, json, MEMBER, SECOND, useStreamFake, wire } from "./harness";

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
};

/** An app the deploy withholds. The mark rides the boot read's own agent row, so the app is the
 *  workspace's either way — this is what the portal draws it out of, not what it may open. */
const HIDDEN_APP = { ...SECOND, name: "wiki", app: "wiki", hidden: true };

test("an app the deploy offers is a row the sidebar pins itself", async () => {
  render(
    <App
      agents={[AGENT, { ...HIDDEN_APP, hidden: false }]}
      member={MEMBER}
      surfaces={ALL_SURFACES}
      onAgents={() => {}}
    />,
  );

  const rail = await screen.findByRole("navigation", { name: "Workspace" });
  expect(within(rail).getByRole("button", { name: "Wiki" })).toBeTruthy();
});

test("an app the deploy withholds is no such row", async () => {
  render(
    <App agents={[AGENT, HIDDEN_APP]} member={MEMBER} surfaces={ALL_SURFACES} onAgents={() => {}} />,
  );

  const rail = await screen.findByRole("navigation", { name: "Workspace" });
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
});

test("the administration gear goes where the deploy withholds it, admin or not", async () => {
  const withheld = render(
    <App agents={[AGENT]} member={ADMIN} surfaces={WITHHELD} onAgents={() => {}} />,
  );
  await waitFor(() => expect(screen.queryByRole("button", { name: "Sign out" })).toBeTruthy());
  expect(screen.queryByRole("button", { name: "Administration" })).toBeNull();
  withheld.unmount();

  render(<App agents={[AGENT]} member={ADMIN} surfaces={ALL_SURFACES} onAgents={() => {}} />);
  expect(await screen.findByRole("button", { name: "Administration" })).toBeTruthy();
});
