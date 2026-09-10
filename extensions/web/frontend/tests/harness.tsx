import { readFileSync } from "node:fs";
import { join } from "node:path";

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState, type ReactNode } from "react";
import { expect, vi } from "vitest";

import { NARROW } from "@/lib/narrow";
import { TooltipProvider } from "@/components/ui/tooltip";
import { TabbedPane } from "@/views/TabbedPane";
import { SECTION_VIEWS, WORKSPACE_VIEWS, type PaneView } from "@/views/registry";
import {
  type Section,
  type WorkspacePlace,
  type WorkspaceTab,
  WORKSPACE_TABS,
} from "@/lib/route";
import type { ChatRow } from "@/lib/rail";

/** A tooltip reads its delay from a provider above it and raises without one. */
function Shell({ children }: { children: ReactNode }) {
  return <TooltipProvider>{children}</TooltipProvider>;
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

export async function goTo(label: string) {
  await userEvent.click(await screen.findByRole("tab", { name: label }));
}

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
 *  prose lives in more than one element. `saying` matches the innermost whose whole text is that line. */
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

/** A pattern matches anywhere in the url, except that one ending in `$` matches only a path ending
 *  there. Both are load-bearing: unanchored, `/conversations` also answers `/conversations/<id>/slots`. */
export function wire(routes: Record<string, Route>) {
  const calls: string[] = [];
  const table: Record<string, Route> = {
    "/api/chats": () => json({ chats: [] }),
    "/objects/conversation$": () => json({ objects: [] }),
    "/api/agents/status": () => json({ statuses: [] }),
    "/connector-catalog": () => json({ providers: [], after: null }),
    "/connections": () => json({ connections: [] }),
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

export const CHAT_APP = {
  ...AGENT,
  id: CHAT_APP_ID,
  name: "chat",
  main: false,
  icon: "aten",
  app: "chat",
};

export const MEMBER = { id: "m1", email: "member@example.com", admin: false };

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
  audience: "member:m1",
  member_email: "member@example.com",
  mine: true,
  speaker: null,
};

export const json = (payload: unknown) => Response.json(payload);

type RailRow = ChatRow;

export const conversationObject = ({ conversation_id, ...row }: RailRow) => ({
  name: conversation_id,
  ...row,
});

export const chatsOnWire = (rows: RailRow[]): Record<string, Route> => ({
  "/objects/conversation$": () => json({ objects: rows.map(conversationObject) }),
  "/api/chats": () => json({ chats: rows }),
});

export const RADAR_TOUR = "What this workspace can do";
export const NO_TASKS = "No scheduled task is visible to you.";
export const NO_TRIGGERS = "No source trigger is visible to you.";
export const NO_ARTIFACTS = "A file or site an app makes in a conversation is listed here.";

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

export async function openRow(name: string): Promise<void> {
  await pressRow(name);
}

export function agentIndex(): Promise<HTMLElement> {
  return screen.findByRole("navigation", { name: "Apps" });
}

export async function openNewApplication(): Promise<void> {
  await userEvent.click(await screen.findByRole("button", { name: "Create app" }));
}

export const FRESH = "New conversation";

export function openConversation(agentId: string, conversationId: string): void {
  location.hash = "#/agents/" + agentId + "?open=" + conversationId;
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}

export function audienceMark(): HTMLElement {
  const mark = document.body.querySelector("[data-slot=audience]");
  if (!mark) throw new Error("no audience marker is drawn");
  return mark as HTMLElement;
}

export async function heldConversation(app = "Assistant"): Promise<string> {
  const pane = await screen.findByRole("region", { name: app });
  const band = pane.querySelector("[data-slot=header] h2");
  if (!band) throw new Error("the half draws no band");
  return String(band.textContent);
}

export async function openAgentRow(name: string): Promise<void> {
  const index = await agentIndex();
  const row = new RegExp("^" + name);
  await userEvent.click(await within(index).findByRole("button", { name: row }));
}

export async function openAgentSettings(
  name = "Assistant",
  tab: "Settings" | "Connectors" | "Scheduled" = "Settings",
): Promise<HTMLElement> {
  await userEvent.click(await screen.findByRole("button", { name: "Menu for " + name }));
  await userEvent.click(await screen.findByRole("menuitem", { name: tab }));
  return await screen.findByRole("dialog");
}

export async function pressRow(name: string): Promise<void> {
  const row = (await screen.findAllByText(name)).map((node) => node.closest("tr")).find(Boolean);
  if (!row) throw new Error("no row named " + name);
  await userEvent.click(row);
}

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

/** The one number here that is not a token, because no token states what a desktop is. */
const DESKTOP = 1280;

const TOKENS = readFileSync(join(import.meta.dirname, "..", "src", "theme.css"), "utf8");

function token(name: string): number {
  const declared = new RegExp("^\\s*" + name + ":\\s*(\\d+)px;", "m").exec(TOKENS);
  if (!declared) throw new Error("theme.css declares no " + name);
  return Number(declared[1]);
}

/** `Page` reserves the scrollbar with `scrollbar-gutter-stable`. Blink's thin scrollbar, measured in the
 *  running portal: a table summing to the gutter budget exactly still scrolls without it. */
const SCROLLBAR_GUTTER = 11;

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

/** It rides a custom property rather than `min-width` itself, because a phone stacks the table into
 *  records and lifts the floor the tracks needed. */
export function declaredFloor(table: Element): string {
  return (table as HTMLTableElement).style.getPropertyValue("--table-floor");
}

export function tableFloors(): string[] {
  return [...document.querySelectorAll("table")].map(declaredFloor);
}
