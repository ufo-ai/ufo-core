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
  apps: false,
  memory: false,
  radar: false,
  "community-skills": false,
  "installed-skills": false,
  "app-store": false,
};

const HIDDEN_APP = { ...SECOND, name: "wiki", app: "wiki", hidden: true };

test("a withheld app still opens from its own address", async () => {
  location.hash = "#/agents/" + HIDDEN_APP.id;
  render(
    <App agents={[AGENT, HIDDEN_APP]} member={MEMBER} surfaces={ALL_SURFACES} onAgents={() => {}} />,
  );

  expect(await screen.findByRole("region", { name: "Wiki" })).toBeTruthy();
});

test("a withheld workspace screen loses its tab", async () => {
  location.hash = "#/workspace/usage";
  render(<App agents={[AGENT]} member={MEMBER} surfaces={WITHHELD} onAgents={() => {}} />);

  await waitFor(() => expect(screen.queryByRole("tab", { name: "Usage" })).toBeTruthy());
  expect(screen.queryByRole("tab", { name: "Apps" })).toBeNull();
  expect(screen.queryByRole("tab", { name: "Team" })).toBeNull();
  expect(screen.queryByRole("tab", { name: "Memory" })).toBeNull();
  expect(screen.queryByRole("tab", { name: "Skills" })).toBeNull();
});

test("the skills tab stands while either of its two panels is offered", async () => {
  location.hash = "#/workspace/usage";
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

test("offered workspace screens keep their tabs and connections keeps its section row", async () => {
  location.hash = "#/workspace/team";
  render(<App agents={[AGENT]} member={MEMBER} surfaces={ALL_SURFACES} onAgents={() => {}} />);

  expect(await screen.findByRole("tab", { name: "Memory" })).toBeTruthy();
  expect(screen.queryByRole("tab", { name: "Connectors" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Connections" }));
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

test("the settings row opens the first workspace tab the member is drawn", async () => {
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

  await userEvent.click(await screen.findByRole("button", { name: "Settings" }));

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

test("the radar row and its palette place are withheld with the flag off", async () => {
  location.hash = "";
  wire({ "/transcript": () => json({ messages: [] }) });
  render(
    <App
      agents={[AGENT]}
      member={MEMBER}
      surfaces={{ ...ALL_SURFACES, radar: false }}
      onAgents={() => {}}
    />,
  );

  const rail = await screen.findByRole("navigation", { name: "Workspace" });
  expect(within(rail).queryByRole("button", { name: "Radar" })).toBeNull();
  expect(within(rail).getByRole("button", { name: "Artifacts" })).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Search" }));
  expect(screen.queryByRole("option", { name: "Radar" })).toBeNull();
});

test("the radar row stands with the flag on, and its address opens either way", async () => {
  location.hash = "";
  wire({ "/transcript": () => json({ messages: [] }) });
  const drawn = render(
    <App agents={[AGENT]} member={MEMBER} surfaces={ALL_SURFACES} onAgents={() => {}} />,
  );

  const rail = await screen.findByRole("navigation", { name: "Workspace" });
  await userEvent.click(within(rail).getByRole("button", { name: "Radar" }));
  await waitFor(() => expect(location.hash).toBe("#/radar"));
  drawn.unmount();

  render(
    <App
      agents={[AGENT]}
      member={MEMBER}
      surfaces={{ ...ALL_SURFACES, radar: false }}
      onAgents={() => {}}
    />,
  );

  await waitFor(() => expect(screen.queryByText("This link is not valid.")).toBeNull());
  expect(parseHash(location.hash)).toEqual({ kind: "section", section: "radar", place: {} });
});

test("the apps tab, the app index and the store are withheld with the flag off", async () => {
  location.hash = "#/agents";
  const index = render(
    <App
      agents={[AGENT]}
      member={ADMIN}
      surfaces={{ ...ALL_SURFACES, apps: false }}
      onAgents={() => {}}
    />,
  );
  expect(await screen.findByText("This link is not valid.")).toBeTruthy();
  index.unmount();

  location.hash = "#/agents/store";
  const store = render(
    <App
      agents={[AGENT]}
      member={ADMIN}
      surfaces={{ ...ALL_SURFACES, apps: false }}
      onAgents={() => {}}
    />,
  );
  expect(await screen.findByText("This link is not valid.")).toBeTruthy();
  store.unmount();

  location.hash = "#/workspace/apps";
  render(
    <App
      agents={[AGENT]}
      member={ADMIN}
      surfaces={{ ...ALL_SURFACES, apps: false }}
      onAgents={() => {}}
    />,
  );
  expect(await screen.findByText("This link is not valid.")).toBeTruthy();
  expect(screen.queryByRole("tab", { name: "Apps" })).toBeNull();
});

test("the apps tab and the app index stand with the flag on", async () => {
  location.hash = "#/workspace/apps";
  render(<App agents={[AGENT]} member={ADMIN} surfaces={ALL_SURFACES} onAgents={() => {}} />);

  expect(await screen.findByRole("tab", { name: "Apps" })).toBeTruthy();
  expect(screen.queryByText("This link is not valid.")).toBeNull();
});

test("the palette offers the apps index and the store only where both flags stand", async () => {
  location.hash = "";
  wire({ "/transcript": () => json({ messages: [] }) });
  const withheld = render(
    <App
      agents={[AGENT]}
      member={ADMIN}
      surfaces={{ ...ALL_SURFACES, apps: false }}
      onAgents={() => {}}
    />,
  );

  await userEvent.click(await screen.findByRole("button", { name: "Search" }));
  expect(screen.queryByRole("option", { name: "Apps" })).toBeNull();
  expect(screen.queryByRole("option", { name: "App Store" })).toBeNull();
  withheld.unmount();

  render(<App agents={[AGENT]} member={ADMIN} surfaces={ALL_SURFACES} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Search" }));
  expect(await screen.findByRole("option", { name: "Apps" })).toBeTruthy();
  expect(screen.getByRole("option", { name: "App Store" })).toBeTruthy();
});
