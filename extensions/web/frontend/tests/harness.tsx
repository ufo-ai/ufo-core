import { readFileSync } from "node:fs";
import { join } from "node:path";

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { expect, vi } from "vitest";

import { NARROW } from "@/App";
import { TabbedPane } from "@/views/TabbedPane";
import { SECTION_VIEWS, WORKSPACE_VIEWS } from "@/views/registry";
import {
  WORKSPACE_TABS,
  type Section,
  type WorkspacePlace,
  type WorkspaceTab,
} from "@/lib/route";

export function PlacedWorkspace({ view }: { view: WorkspaceTab }) {
  const [placed, setPlaced] = useState<{ view: WorkspaceTab; place: WorkspacePlace }>({
    view,
    place: {},
  });
  if (placed.view !== view) setPlaced({ view, place: {} });
  return (
    <TabbedPane
      title="Workspace"
      group="workspace"
      tabs={WORKSPACE_TABS}
      views={WORKSPACE_VIEWS}
      view={view}
      place={placed.place}
      onPlace={(next, place) => setPlaced({ view: next, place })}
    />
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
    <TabbedPane
      title={SECTION_VIEWS[section].label}
      group="section"
      tabs={[section]}
      views={SECTION_VIEWS}
      view={section}
      place={placed.place}
      onPlace={(next, place) => setPlaced({ section: next, place })}
    />
  );
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
    "/api/agents/status": () => json({ statuses: [] }),
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

export const ADMIN_AGENT = {
  ...AGENT,
  internet_access_allowed: true,
  installations: [],
  web_audience: [],
};

export const SECOND = {
  ...AGENT,
  id: SECOND_ID,
  name: "second",
  main: false,
  icon: "aten",
};

export const MEMBER = { id: "m1", email: "member@example.com", admin: false };

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
  spec: { icon: "propylon", model: "opus", reasoning: "high", internet_access_allowed: true },
  spec_schema: {
    properties: {
      model: { type: "string" },
      reasoning: { type: "string", enum: ["low", "high"] },
      internet_access_allowed: { type: "boolean" },
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

export const NO_RUNS =
  "Each scheduled run reports here: the reply it closed with and the files it shared.";
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

/** The agents screen's index column. */
export function agentIndex(): Promise<HTMLElement> {
  return screen.findByRole("navigation", { name: "Apps" });
}

/** Open one agent from the index: the row is the control, named by the text it carries. */
/** What the app pane's conversation half is headed by before a conversation names it, which is
 *  also the switcher's own control while nothing is open. */
export const FRESH = "New conversation";

/** Open one of an app's conversations: the title of the one the half holds is the control, and the
 *  conversations it lists are what a press picks between. */
export async function pickConversation(held: string, wanted: string): Promise<void> {
  await userEvent.click(await screen.findByRole("button", { name: held }));
  await userEvent.click(await screen.findByRole("menuitemradio", { name: wanted }));
}

export async function openAgentRow(name: string): Promise<void> {
  const index = await agentIndex();
  await userEvent.click(within(index).getByRole("button", { name: new RegExp("^" + name) }));
}

/** An agent's settings, which stand in a dialog the gear in the agent's own pane header opens
 *  rather than on a tab of the agent's page. The dialog opens on Settings; `tab` reaches the
 *  other three. */
export async function openAgentSettings(
  name = "Assistant",
  tab: "Settings" | "Connectors" | "Skills" | "Scheduled" = "Settings",
): Promise<HTMLElement> {
  const pane = await screen.findByRole("region", { name });
  await userEvent.click(within(pane).getByRole("button", { name: "Settings for " + name }));
  const dialog = await screen.findByRole("dialog");
  if (tab !== "Settings") {
    await userEvent.click(within(dialog).getByRole("tab", { name: tab }));
  }
  return dialog;
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
  return within(card).getByRole("button", { name: "View" });
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
