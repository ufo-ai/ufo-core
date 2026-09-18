import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { parseHash } from "@/lib/route";
import { ALL_SURFACES } from "@/lib/surfaces";
import type { Surfaces } from "@/lib/types";

import { AGENT, atPhoneWidth, json, MEMBER, SECOND, useStreamFake, wire } from "./harness";

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
  apps: false,
  email: false,
  memory: false,
  radar: false,
  "community-skills": false,
  "installed-skills": false,
  "app-store": false,
};

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

test("offered workspace screens keep their tabs and connectors stays out of the strip", async () => {
  location.hash = "#/workspace/team";
  render(<App agents={[AGENT]} member={MEMBER} surfaces={ALL_SURFACES} onAgents={() => {}} />);

  expect(await screen.findByRole("tab", { name: "Memory" })).toBeTruthy();
  expect(screen.queryByRole("tab", { name: "Connectors" })).toBeNull();
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

test("the apps flag takes away every control that opens an apps address", async () => {
  location.hash = "";
  const desk = render(
    <App
      agents={[AGENT]}
      member={MEMBER}
      surfaces={{ ...ALL_SURFACES, apps: false }}
      onAgents={() => {}}
    />,
  );

  const rail = within(await screen.findByRole("navigation", { name: "Tabs" }));
  expect(rail.queryByRole("button", { name: "New app" })).toBeNull();
  desk.unmount();

  location.hash = "";
  render(<App agents={[AGENT]} member={MEMBER} surfaces={ALL_SURFACES} onAgents={() => {}} />);

  const offered = within(await screen.findByRole("navigation", { name: "Tabs" }));
  expect(offered.getByRole("button", { name: "New app" })).toBeTruthy();
});

test("the sidebar's create-app row goes with the apps flag", async () => {
  atPhoneWidth();
  const off = render(
    <App
      agents={[AGENT]}
      member={MEMBER}
      surfaces={{ ...ALL_SURFACES, apps: false }}
      onAgents={() => {}}
    />,
  );

  const withheld = within(await openWorkspaceColumn());
  expect(withheld.getByRole("button", { name: "New chat" })).toBeTruthy();
  expect(withheld.queryByRole("button", { name: "Create app" })).toBeNull();
  off.unmount();

  render(<App agents={[AGENT]} member={MEMBER} surfaces={ALL_SURFACES} onAgents={() => {}} />);

  const offered = within(await openWorkspaceColumn());
  expect(offered.getByRole("button", { name: "Create app" })).toBeTruthy();
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

test("no app control is drawn while the flag withholds apps", async () => {
  atPhoneWidth();
  render(
    <App
      agents={[AGENT]}
      member={ADMIN}
      surfaces={{ ...ALL_SURFACES, apps: false }}
      onAgents={() => {}}
    />,
  );

  const rail = await openWorkspaceColumn();
  const names = within(rail)
    .getAllByRole("button")
    .map((row) => row.getAttribute("aria-label") ?? row.textContent);
  expect(names).not.toContain("Create app");
  expect(names).not.toContain("Apps");
  expect(screen.queryByRole("button", { name: "New app" })).toBeNull();
});

test("a deploy with no send seam loses the notifications tab, and one that sends keeps it", async () => {
  location.hash = "#/workspace/usage";
  render(<App agents={[AGENT]} member={MEMBER} surfaces={WITHHELD} onAgents={() => {}} />);

  await waitFor(() => expect(screen.queryByRole("tab", { name: "Usage" })).toBeTruthy());
  expect(screen.queryByRole("tab", { name: "Notifications" })).toBeNull();
});

test("a deploy that sends offers the notifications tab", async () => {
  location.hash = "#/workspace/usage";
  render(
    <App
      agents={[AGENT]}
      member={MEMBER}
      surfaces={{ ...WITHHELD, email: true }}
      onAgents={() => {}}
    />,
  );

  await waitFor(() => expect(screen.queryByRole("tab", { name: "Notifications" })).toBeTruthy());
});


test.each([false, true])("Admin is visible only for an admin: %s", async (admin) => {
  location.hash = "#/workspace/apps";
  render(<App agents={[AGENT]} member={{ ...MEMBER, admin }} surfaces={ALL_SURFACES} onAgents={() => {}} />);
  await screen.findByRole("tab", { name: "Apps" });
  expect(Boolean(screen.queryByRole("tab", { name: "Admin" }))).toBe(admin);
});
