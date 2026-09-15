import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { ALL_SURFACES } from "@/lib/surfaces";

import {
  AGENT,
  CONVO_ID,
  MEMBER,
  SECOND_ID,
  TURN_ID,
  json,
  openStore,
  useStreamFake,
  wire,
} from "./harness";

const ADMIN = { ...MEMBER, admin: true };
const RADAR_ID = "44444444-4444-4444-8444-444444444444";
const WIKI_ID = "66666666-6666-4666-8666-666666666666";
const MEETINGS_ID = "77777777-7777-4777-8777-777777777777";

const RADAR = {
  id: RADAR_ID,
  name: "radar",
  model: "auto",
  main: false,
  icon: "radar",
  app: "radar",
  purpose: "Shows what your scheduled work found.",
};

const RESEARCH = { id: SECOND_ID, name: "research", model: "auto", main: false, icon: "aten" };

const WIKI = {
  id: WIKI_ID,
  name: "wiki",
  object: "~archived-" + WIKI_ID,
  icon: "book",
  purpose: "Keeps the workspace's pages.",
  app: "wiki",
  archived_at: "2026-08-20T12:00:00Z",
};

const MEETINGS = {
  id: MEETINGS_ID,
  name: "meetings",
  object: "~archived-" + MEETINGS_ID,
  icon: "calendar",
  purpose: "Prepares you for the meetings on your calendar.",
  app: "meetings",
  hidden: true,
  archived_at: "2026-08-21T12:00:00Z",
};

const SCRATCH = {
  id: "99999999-9999-4999-8999-999999999999",
  name: "scratch",
  object: "~archived-99999999-9999-4999-8999-999999999999",
  icon: "aten",
  archived_at: "2026-08-22T12:00:00Z",
};

const RESTORE_VIEW = {
  name: "restore_application",
  description: "",
  input_schema: {},
  call: { kind: "agent", name: WIKI.object, action: "restore_application", input: {} },
  label: "Restore",
};

function listed(store: HTMLElement): string[] {
  return within(store)
    .getAllByRole("row")
    .slice(1)
    .map((row) => within(row).getAllByRole("cell")[0].textContent ?? "");
}

beforeEach(() => {
  location.hash = "";
  useStreamFake();
});

test("the store lists the deploy's apps by state, and the act that builds one last", async () => {
  wire({ "/transcript": () => json({ messages: [] }) });
  location.hash = "#/agents/store";
  render(
    <App
      agents={[AGENT, RADAR, RESEARCH]}
      archived={[WIKI, MEETINGS, SCRATCH]}
      member={ADMIN}
      onAgents={() => {}}
    />,
  );

  const store = await screen.findByRole("region", { name: "App Store" });
  expect(screen.getByRole("heading", { name: "App Store" })).toBeTruthy();
  expect(listed(store)).toEqual(["Radar", "Wiki", "App Creator"]);
  expect(within(store).getByText("Shows what your scheduled work found.")).toBeTruthy();
  expect(within(store).getByText("Keeps the workspace's pages.")).toBeTruthy();

  const radar = within(store).getByRole("row", { name: /^Radar/ });
  expect(within(radar).getByRole("button", { name: "Remove" })).toBeTruthy();
  expect(within(radar).queryByRole("button", { name: "Install" })).toBeNull();
  const wiki = within(store).getByRole("row", { name: /^Wiki/ });
  expect(within(wiki).getByRole("button", { name: "Install" })).toBeTruthy();
  expect(within(wiki).queryByRole("button", { name: "Remove" })).toBeNull();
  const creator = within(store).getByRole("row", { name: /^App Creator/ });
  expect(within(creator).queryByRole("button")).toBeNull();
  expect(document.title).toBe("App Store · ufo");
});

test("an installed app opens from its row", async () => {
  wire({ "/transcript": () => json({ messages: [] }) });
  location.hash = "#/agents/store";
  render(<App agents={[AGENT, RADAR]} archived={[]} member={ADMIN} onAgents={() => {}} />);
  const store = await screen.findByRole("region", { name: "App Store" });

  await userEvent.click(within(store).getByRole("row", { name: /^Radar/ }));

  expect(location.hash).toBe("#/agents/" + RADAR_ID);
  expect(await screen.findByRole("region", { name: "Radar" })).toBeTruthy();
});

test("installing a removed app restores its row under the name it held, and re-reads the apps", async () => {
  const posted: unknown[] = [];
  const onAgents = vi.fn();
  wire({
    ["/actions/agent/" + WIKI.object + "$"]: () => json({ actions: [RESTORE_VIEW] }),
    ["/actions/agent/" + WIKI.object + "/restore_application"]: (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Applied." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/store";
  render(<App agents={[AGENT, RADAR]} archived={[WIKI]} member={ADMIN} onAgents={onAgents} />);
  const store = await screen.findByRole("region", { name: "App Store" });

  const wiki = within(store).getByRole("row", { name: /^Wiki/ });
  await userEvent.click(within(wiki).getByRole("button", { name: "Install" }));

  await waitFor(() => expect(onAgents).toHaveBeenCalledOnce());
  expect(posted).toEqual([{ new_name: "wiki" }]);
  expect(location.hash).toBe("#/agents/store");
});

test("a refused install states the refusal beside the rows and re-reads nothing", async () => {
  const onAgents = vi.fn();
  wire({
    ["/actions/agent/" + WIKI.object + "$"]: () => json({ actions: [RESTORE_VIEW] }),
    ["/actions/agent/" + WIKI.object + "/restore_application"]: () =>
      json({ applied: false, message: "an agent named 'wiki' already exists" }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/store";
  render(<App agents={[AGENT, RADAR]} archived={[WIKI]} member={ADMIN} onAgents={onAgents} />);
  const store = await screen.findByRole("region", { name: "App Store" });

  await userEvent.click(within(store).getByRole("button", { name: "Install" }));

  expect(await within(store).findByText("an agent named 'wiki' already exists")).toBeTruthy();
  expect(onAgents).not.toHaveBeenCalled();
  expect(within(store).getByRole("button", { name: "Install" })).toBeTruthy();
});

test("removing an installed app archives it on its own lane after a confirming press, and stays in the store", async () => {
  const posted: { url: string; body: unknown }[] = [];
  const onAgents = vi.fn();
  wire({
    ["/agents/" + RADAR_ID + "/intents"]: (url, init) => {
      posted.push({ url, body: JSON.parse(String(init?.body)) });
      return json({ applied: true, message: "Applied." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/store";
  render(<App agents={[AGENT, RADAR]} archived={[]} member={ADMIN} onAgents={onAgents} />);
  const store = await screen.findByRole("region", { name: "App Store" });

  const radar = within(store).getByRole("row", { name: /^Radar/ });
  await userEvent.click(within(radar).getByRole("button", { name: "Remove" }));
  expect(posted).toEqual([]);
  expect(location.hash).toBe("#/agents/store");
  await userEvent.click(within(radar).getByRole("button", { name: "Confirm remove" }));

  await waitFor(() => expect(onAgents).toHaveBeenCalledOnce());
  expect(posted).toEqual([
    {
      url: "/surface/web/agents/" + RADAR_ID + "/intents",
      body: { verb: "delete", kind: "agent", name: "radar" },
    },
  ]);
  expect(location.hash).toBe("#/agents/store");
});

test("a member who is not an admin reads the installed apps and the build act, and no act on the apps", async () => {
  wire({ "/transcript": () => json({ messages: [] }) });
  location.hash = "#/agents/store";
  render(<App agents={[AGENT, RADAR]} archived={[]} member={MEMBER} onAgents={() => {}} />);
  const store = await screen.findByRole("region", { name: "App Store" });

  expect(listed(store)).toEqual(["Radar", "App Creator"]);
  expect(within(store).queryByRole("button", { name: "Remove" })).toBeNull();
  expect(within(store).queryByRole("button", { name: "Install" })).toBeNull();
});

test("the App Creator row raises the wizard from the store", async () => {
  wire({
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "Finances dash" }),
    "/slots/tasks": () =>
      json({ type: "tasks", title: "", tasks: [], total_count: 0, completed_count: 0, truncated: false }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT, RADAR]} archived={[]} member={MEMBER} onAgents={() => {}} />);

  const store = await openStore();
  await userEvent.click(within(store).getByRole("row", { name: /^App Creator/ }));

  expect(await screen.findByRole("region", { name: "App Creator" })).toBeTruthy();
  expect(location.hash).toBe("#/agents/builder");
});

test("a deploy that withholds the store keeps its address, and its list ends in App Creator", async () => {
  wire({
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "Finances dash" }),
    "/slots/tasks": () =>
      json({ type: "tasks", title: "", tasks: [], total_count: 0, completed_count: 0, truncated: false }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/store";
  render(
    <App
      agents={[AGENT, RADAR]}
      archived={[WIKI]}
      member={ADMIN}
      surfaces={{ ...ALL_SURFACES, "app-store": false }}
      onAgents={() => {}}
    />,
  );

  const store = await screen.findByRole("region", { name: "App Store" });
  expect(listed(store)).toEqual(["Radar", "Wiki", "App Creator"]);
});
