import { readFileSync } from "node:fs";
import { join } from "node:path";

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState, type ReactNode } from "react";
import { expect, vi } from "vitest";

import { NARROW } from "@/App";
import { TooltipProvider } from "@/components/ui/tooltip";
import { TabbedPane } from "@/views/TabbedPane";
import { SECTION_VIEWS, WORKSPACE_VIEWS, type PaneView } from "@/views/registry";
import {
  type Section,
  type WorkspacePlace,
  type WorkspaceTab,
  WORKSPACE_TABS,
} from "@/lib/route";
import { WorkspaceId } from "@/lib/audience";
import type { ChatRow } from "@/lib/rail";

export const WORKSPACE_ID = "31b26b98-0000-4000-8000-000000000031";

/** The shell's own providers, so a view mounted alone draws what it draws inside the app. A tooltip
 *  reads its delay from a provider above it and raises without one. */
function Shell({ children }: { children: ReactNode }) {
  return (
    <WorkspaceId.Provider value={WORKSPACE_ID}>
      <TooltipProvider>{children}</TooltipProvider>
    </WorkspaceId.Provider>
  );
}

export function PlacedWorkspace({ view }: { view: WorkspaceTab }) {
  const [placed, setPlaced] = useState<{ view: WorkspaceTab; place: WorkspacePlace }>({
    view,
    place: {},
  });
  if (placed.view !== view) setPlaced({ view, place: {} });
  return (
    <Shell>
    <TabbedPane
      group="workspace"
      tabs={WORKSPACE_TABS}
      views={WORKSPACE_VIEWS}
      view={view}
      place={placed.place}
      onPlace={(next, place) => setPlaced({ view: next, place })}
    />
    </Shell>
  );
}

export function PlacedSection({
  section,
  place = {},
}: {
  section: Section;
  place?: WorkspacePlace;
}) {
  const [placed, setPlaced] = useState<{ section: Section; place: WorkspacePlace }>({
    section,
    place,
  });
  if (placed.section !== section) setPlaced({ section, place: {} });
  return (
    <Shell>
    <TabbedPane
      group="section"
      tabs={["connectors"]}
      views={SECTION_VIEWS as Record<Section, PaneView>}
      view={section}
      place={placed.place}
      onPlace={(next, place) => setPlaced({ section: next, place })}
    />
    </Shell>
  );
}

/** The destination's own name is the control that moves between destinations, so a test changing
 *  destination presses the name and picks out of the menu under it, exactly as a member does. */
export async function goTo(label: string) {
  await userEvent.click(await screen.findByRole("tab", { name: label }));
}

/** The destination the shell is standing on, which its heading names. */
export function destination(): string {
  return screen.getByRole("heading", { level: 1 }).textContent ?? "";
}

export class StreamFake {
  static opened: StreamFake[] = [];
  readyState = 0;
  url: string;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  private listeners = new Map<string, ((event: MessageEvent) => void)[]>();

  static CLOSED = 2;

  constructor(url: string) {
    this.url = url;
    StreamFake.opened.push(this);
  }

  addEventListener(name: string, handler: (event: MessageEvent) => void) {
    this.listeners.set(name, (this.listeners.get(name) ?? []).concat(handler));
  }

  close() {
    this.closed = true;
    this.readyState = StreamFake.CLOSED;
  }

  emit(name: string, data: unknown) {
    const event = { data: JSON.stringify(data) } as MessageEvent;
    if (name === "message") {
      this.onmessage?.(event);
      return;
    }
    for (const handler of this.listeners.get(name) ?? []) handler(event);
  }

  fail() {
    this.onerror?.();
  }

  static reset() {
    StreamFake.opened = [];
  }

  static last(): StreamFake {
    const stream = StreamFake.opened.at(-1);
    if (!stream) throw new Error("no stream was opened");
    return stream;
  }
}

/** A streaming reply is drawn word by word and, at its head, character by character, so one line of
 *  prose lives in more than one element and a word carries the space beside it. `saying` matches the
 *  innermost element whose whole text is that line. */
export function saying(line: string | RegExp) {
  const matches = (text: string) =>
    typeof line === "string" ? text.trim() === line : line.test(text);
  return (_content: string, node: Element | null) =>
    node !== null &&
    matches(node.textContent ?? "") &&
    !Array.from(node.children).some((child) => matches(child.textContent ?? ""));
}

export function useStreamFake() {
  StreamFake.reset();
  vi.stubGlobal("EventSource", StreamFake);
}

/** Draws as a phone does: the shell's own breakpoint answers true, and every other query a component
 *  asks — reduced motion, colour scheme — answers as it does at a desk width. */
export function atPhoneWidth() {
  vi.stubGlobal("matchMedia", (media: string) => ({
    media,
    matches: media === NARROW,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }));
}

export type Route = (url: string, init?: RequestInit) => Response | Promise<Response>;

/** The wire every suite stubs its reads on. A pattern matches anywhere in the url, except that one
 *  ending in `$` matches only a url whose path ends there.
 *
 *  Both halves of that are load-bearing, and each is a bug the other cannot prevent. Substring
 *  matching is why the defaults are tried first: a suite stubbing `/chat` would otherwise answer
 *  `/api/chats` with it and hand the rail a payload holding no chats. And it is why the
 *  conversation index is anchored: unanchored, `/conversations` answers
 *  `/conversations/<id>/slots` and `/conversations/<id>/transcript` too, handing a chat pane an
 *  index where it expected its slots. The defaults are the reads a screen makes whatever the suite
 *  is about, so a suite states only what it is testing. */
export function wire(routes: Record<string, Route>) {
  const calls: string[] = [];
  const table: Record<string, Route> = {
    "/api/chats": () => json({ chats: [] }),
    "/objects/conversation$": () => json({ objects: [] }),
    "/api/agents/status": () => json({ statuses: [] }),
    "/connector-catalog": () => json({ providers: [], after: null }),
    "/homepage": () => json({ state: "none" }),
    "/conversations$": () => json({ conversations: [] }),
    ...routes,
  };
  const matches = (url: string, pattern: string) =>
    pattern.endsWith("$")
      ? url.split("?")[0].endsWith(pattern.slice(0, -1))
      : url.includes(pattern);
  const handler = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push(url);
    for (const [pattern, route] of Object.entries(table)) {
      if (matches(url, pattern)) return route(url, init);
    }
    throw new Error("no route for " + url);
  });
  vi.stubGlobal("fetch", handler);
  return { calls, handler };
}

export const AGENT_ID = "11111111-1111-4111-8111-111111111111";
export const SECOND_ID = "22222222-2222-4222-8222-222222222222";
export const TURN_ID = "33333333-3333-4333-8333-333333333333";
export const CONVO_ID = "55555555-5555-4555-8555-555555555555";
export const ARRIVAL_ID = "88888888-8888-4888-8888-888888888888";

export const AGENT = {
  id: AGENT_ID,
  name: "assistant",
  model: "opus",
  main: true,
  icon: "propylon",
};

export const SECOND = {
  ...AGENT,
  id: SECOND_ID,
  name: "second",
  main: false,
  icon: "aten",
};

export const CHAT_APP_ID = "44444444-4444-4444-8444-444444444444";

/** The shipped chat app's agent: the pane conversations are read and answered in. */
export const CHAT_APP = {
  ...AGENT,
  id: CHAT_APP_ID,
  name: "chat",
  main: false,
  icon: "aten",
  app: "chat",
};

export const MEMBER = { id: "m1", email: "member@example.com", admin: false, workspace_id: WORKSPACE_ID };

/** The agent's settings as the surface states them. The tab holds the agent's connectors too, so
 *  every suite that opens it wires this beside the connection reads. */
export const SETTINGS = {
  agent: {
    name: "assistant",
    main: true,
    surfaces: ["web", "ufo"],
    updated_at: "2026-07-30T12:00:00",
    prompt: "be useful",
    prompt_digest: "abc123",
  },
  spec: {
    icon: "propylon",
    model: "opus",
    reasoning: "high",
    internet_access_allowed: true,
    use_workspace_skills: true,
  },
  spec_schema: {
    properties: {
      model: { type: "string" },
      reasoning: { type: "string", enum: ["low", "high"] },
      internet_access_allowed: { type: "boolean" },
      use_workspace_skills: { type: "boolean", title: "Use workspace skills" },
    },
  },
  models: ["opus", "sonnet"],
  deploy: { sandbox_internet: true },
  audience: [],
};

export const CHAT_ROW = {
  conversation_id: CONVO_ID,
  agent_id: AGENT_ID,
  agent_name: "assistant",
  title: "Pick one thread",
  last_at: "2026-08-01T09:00:00.000Z",
  surface: "web",
  surface_label: null,
  mine: true,
  speaker: null,
};

export const json = (payload: unknown) => Response.json(payload);

type RailRow = ChatRow;

/** One rail row as the conversation kind's index answers it: the same fields under the object
 *  row's shape, named by the conversation id. */
export const conversationObject = ({ conversation_id, ...row }: RailRow) => ({
  name: conversation_id,
  ...row,
});

/** The two reads a seeded rail row answers: the conversation index the rail reads, and the
 *  permalink resolve that names the same rows one at a time. */
export const chatsOnWire = (rows: RailRow[]): Record<string, Route> => ({
  "/objects/conversation$": () => json({ objects: rows.map(conversationObject) }),
  "/api/chats": () => json({ chats: rows }),
});

export const RADAR_TOUR = "What this workspace can do";
export const NO_TASKS = "No scheduled task is visible to you.";
export const NO_TRIGGERS = "No source trigger is visible to you.";
export const NO_ARTIFACTS = "A file or site an app makes in a conversation is listed here.";

/** The kind as the surface states it: the fields sorted, because that is the order `PortalKind`
 *  carries them in, and the index leads with the first of them it draws a column for. */
export const TASK_KIND = {
  kind: "scheduled_task",
  fields: [
    "conversation",
    "id",
    "last_run_at",
    "last_run_status",
    "mine",
    "next_run_at",
    "origin",
    "owner_email",
    "paused",
    "prompt",
  ],
  spec_schema: {
    properties: {
      schedule: { type: "string" },
      prompt: { type: "string", title: "Prompt" },
      paused: { type: "boolean" },
    },
  },
  applies: true,
  deletes: true,
};

/** The kind the lane only ever deletes: a trigger is created in chat, so the portal draws its rows
 *  and the act that ends one, and no act that makes one. */
export const TRIGGER_KIND = {
  kind: "source_trigger",
  fields: ["conversation", "source", "delivery", "origin", "owner_email", "mine"],
  spec_schema: {
    properties: {
      source: { type: "string", title: "Source" },
      delivery: { type: "string", enum: ["current", "per_page"], title: "Delivery" },
    },
  },
  applies: false,
  deletes: true,
};

export const SITE_KIND = {
  kind: "site",
  fields: ["conversation", "created_at", "visibility", "site_url", "owner_email"],
  spec_schema: {
    properties: { visibility: { type: "string", enum: ["private", "workspace", "public"] } },
  },
  applies: false,
  deletes: false,
};

export function objectIndex(kind: unknown, objects: unknown[], next: string | null = null) {
  return json({ ...(kind as object), objects, next_cursor: next });
}

/** A row of the cross-agent object index, which names the agent that owns it. */
export function owned(row: object, agent = AGENT) {
  return { ...row, agent_id: agent.id, agent_name: agent.name };
}

export async function opened(name: string): Promise<HTMLElement[]> {
  await userEvent.click(await screen.findByRole("combobox", { name }));
  return screen.getAllByRole("option");
}

export async function pick(name: string, option: string): Promise<void> {
  await userEvent.click(await screen.findByRole("combobox", { name }));
  await userEvent.click(await screen.findByRole("option", { name: option }));
}

export async function refusedNotice(text: string): Promise<void> {
  const notice = await screen.findByText(text);
  expect(notice.className).toContain("bg-attention");
  expect(notice.className).toContain("[color:var(--color-attention-ink)]");
}

export function fact(label: string): string {
  const term = screen.getByText(label, { selector: "dt" });
  const said = term.nextElementSibling;
  if (!said) throw new Error("no value beside the fact " + label);
  return String(said.textContent);
}

/** Open a table row's own record: the row itself is the control that reaches it. */
export async function openRow(name: string): Promise<void> {
  await pressRow(name);
}

/** The apps index: the sidebar's own list of the workspace's apps, which stands on every screen
 *  rather than behind a flyout the caller has to raise. */
export function agentIndex(): Promise<HTMLElement> {
  return screen.findByRole("navigation", { name: "Apps" });
}

/** Open the list past the run it draws on its own, where an app nobody pinned stands. Asked of a
 *  list already whole, it does nothing. */
export async function expandApps(): Promise<void> {
  const more = screen.queryByRole("button", { name: "More applications" });
  if (more) await userEvent.click(more);
}

/** The act that builds an app, which stands as the last row of the apps list. */
export async function openNewApplication(): Promise<void> {
  await userEvent.click(await screen.findByRole("button", { name: "App Creator" }));
}

/** What the app pane's conversation half is headed by before a conversation names it. */
export const FRESH = "New conversation";

/** Following a link that names one of an app's conversations, which is how the app pane comes to
 *  stand on a conversation it is not already holding: the half picks among none of them, the
 *  address names the one it holds. */
export function openConversation(agentId: string, conversationId: string): void {
  location.hash = "#/agents/" + agentId + "?open=" + conversationId;
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}

/** What the band over an app pane's conversation half names, which is the conversation the half is
 *  holding — `FRESH` where it holds none yet. */
export async function heldConversation(app = "Assistant"): Promise<string> {
  const pane = await screen.findByRole("region", { name: app });
  const band = pane.querySelector("[data-slot=header] h2");
  if (!band) throw new Error("the half draws no band");
  return String(band.textContent);
}

/** Open one agent from the index: the row is the control, named by the text it carries. */

export async function openAgentRow(name: string): Promise<void> {
  const index = await agentIndex();
  const row = new RegExp("^" + name);
  if (!within(index).queryByRole("button", { name: row })) await expandApps();
  await userEvent.click(await within(index).findByRole("button", { name: row }));
}

/** An agent's three reads, which stand in a panel over the screen rather than on a tab of the
 *  agent's page. The menu is found by its own label rather than inside a region, because which half
 *  wears it depends on the app: the homepage titlebar where one is set or building, the
 *  conversation header where the app has none. Each item names the read it opens, so `tab` is the
 *  item pressed rather than a strip inside the panel. */
export async function openAgentSettings(
  name = "Assistant",
  tab: "Settings" | "Connectors" | "Scheduled" = "Settings",
): Promise<HTMLElement> {
  await userEvent.click(await screen.findByRole("button", { name: "Menu for " + name }));
  await userEvent.click(await screen.findByRole("menuitem", { name: tab }));
  return await screen.findByRole("dialog");
}

/** Press a record's own row, where the row itself is the control that opens it. */
export async function pressRow(name: string): Promise<void> {
  const row = (await screen.findAllByText(name)).map((node) => node.closest("tr")).find(Boolean);
  if (!row) throw new Error("no row named " + name);
  await userEvent.click(row);
}

/** Open a list row's own record: the row itself is the control that reaches it. */
export async function pressItem(name: string): Promise<void> {
  const row = (await screen.findAllByText(name)).map((node) => node.closest("li")).find(Boolean);
  if (!row) throw new Error("no item named " + name);
  await userEvent.click(row);
}

export async function viewCard(name: string): Promise<HTMLElement> {
  const card = (await screen.findAllByText(name))
    .map((node) => node.closest("li"))
    .find((entry) => entry);
  if (!card) throw new Error("no card named " + name);
  return within(card).getByRole("button");
}

/** The width the portal is read at. The one number here that is not a token, because no token
 *  states what a desktop is. */
const DESKTOP = 1280;

const TOKENS = readFileSync(join(import.meta.dirname, "..", "src", "theme.css"), "utf8");

function token(name: string): number {
  const declared = new RegExp("^\\s*" + name + ":\\s*(\\d+)px;", "m").exec(TOKENS);
  if (!declared) throw new Error("theme.css declares no " + name);
  return Number(declared[1]);
}

/** `Page` reserves the scrollbar with `scrollbar-gutter-stable`, so the column a table is read in
 *  is always this much narrower than the gutters alone imply. Blink's thin scrollbar, measured in
 *  the running portal: a table summing to the gutter budget exactly still scrolls without it. */
const SCROLLBAR_GUTTER = 11;

/** Whether a table's declared floor clears the column a desktop leaves it. The tracks are fixed
 *  pixels, so a table whose tracks outrun its page holds its width and the column scrolls sideways —
 *  and what falls off the right is the act the row is pressed by. Every width but `DESKTOP` is read
 *  from `theme.css`, so a token change moves the assertion with it. */
export function pageFits(minWidth: string): boolean {
  const tracks = minWidth
    .replace(/^calc\(|\)$/g, "")
    .split("+")
    .reduce((total, term) => {
      const [count, name] = term.split("*").map((part) => part.trim());
      return total + Number(count) * token(name.replace(/^var\(|\)$/g, ""));
    }, 0);
  const shell = token("--container-sidebar") + 2 * token("--size-page-gutter") + SCROLLBAR_GUTTER;
  return shell + tracks <= DESKTOP;
}

/** The floor one table declares. It rides a custom property rather than `min-width` itself, because
 *  a phone stacks the table into records and lifts the floor the tracks needed. */
export function declaredFloor(table: Element): string {
  return (table as HTMLTableElement).style.getPropertyValue("--table-floor");
}

/** Every table on a screen, by the floor it declares. */
export function tableFloors(): string[] {
  return [...document.querySelectorAll("table")].map(declaredFloor);
}
