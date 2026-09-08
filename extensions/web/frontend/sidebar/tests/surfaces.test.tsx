import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { parseHash } from "@/lib/route";
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
  team: false,
  memory: false,
  "community-skills": false,
  "installed-skills": false,
  "app-store": false,
};

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
  location.hash = "#/workspace/apps";
  render(<App agents={[AGENT]} member={MEMBER} surfaces={WITHHELD} onAgents={() => {}} />);

  await waitFor(() => expect(screen.queryByRole("tab", { name: "Apps" })).toBeTruthy());
  expect(screen.queryByRole("tab", { name: "Team" })).toBeNull();
  expect(screen.queryByRole("tab", { name: "Memory" })).toBeNull();
  expect(screen.queryByRole("tab", { name: "Skills" })).toBeNull();
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

test("offered workspace screens keep their tabs and connectors keeps its section row", async () => {
  location.hash = "#/workspace/team";
  render(<App agents={[AGENT]} member={MEMBER} surfaces={ALL_SURFACES} onAgents={() => {}} />);

  expect(await screen.findByRole("tab", { name: "Memory" })).toBeTruthy();
  expect(screen.queryByRole("tab", { name: "Connectors" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Connectors" }));
  await waitFor(() => expect(location.hash).toBe("#/connectors"));
  expect(screen.queryByRole("tab", { name: "Sources" })).toBeNull();
});

/** A withheld screen loses its tab and keeps its address, and a destination opens on the first tab this
 *  member is drawn. */
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

test("the workspace row lands a member on the first tab they are drawn", async () => {
  location.hash = "";
  wire({ "/transcript": () => json({ messages: [] }) });
  render(
    <App
      agents={[AGENT]}
      member={MEMBER}
      surfaces={{ ...ALL_SURFACES, team: false }}
      onAgents={() => {}}
    />,
  );

  await userEvent.click(await screen.findByRole("button", { name: "Workspace" }));

  expect(parseHash(location.hash)).toEqual({ kind: "workspace", view: "apps", place: {} });
  expect(await screen.findByRole("tab", { name: "Apps" })).toBeTruthy();
});

test("the palette's workspace row opens the first tab the member is drawn", async () => {
  location.hash = "";
  wire({ "/transcript": () => json({ messages: [] }) });
  render(
    <App
      agents={[AGENT]}
      member={MEMBER}
      surfaces={{ ...ALL_SURFACES, team: false }}
      onAgents={() => {}}
    />,
  );

  await userEvent.click(await screen.findByRole("button", { name: "Search" }));
  await userEvent.click(await screen.findByRole("option", { name: "Workspace" }));

  expect(parseHash(location.hash)).toEqual({ kind: "workspace", view: "apps", place: {} });
});
