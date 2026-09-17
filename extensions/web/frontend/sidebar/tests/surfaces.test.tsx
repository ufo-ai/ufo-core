import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { parseHash } from "@/lib/route";
import { ALL_SURFACES } from "@/lib/surfaces";

import { AGENT, json, MEMBER, useStreamFake, wire } from "../../tests/harness";

beforeEach(() => {
  useStreamFake();
  location.hash = "";
  wire({
    "/api/members": () => json({ members: [], invitations: [] }),
    "/objects/": () => json({ objects: [] }),
    "/transcript": () => json({ messages: [] }),
  });
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

test("the notifications tab is withheld where the deploy sends none", async () => {
  location.hash = "#/workspace/apps";
  render(
    <App
      agents={[AGENT]}
      member={MEMBER}
      surfaces={{ ...ALL_SURFACES, email: false }}
      onAgents={() => {}}
    />,
  );

  expect(await screen.findByRole("tab", { name: "Apps" })).toBeTruthy();
  expect(screen.queryByRole("tab", { name: "Notifications" })).toBeNull();
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
