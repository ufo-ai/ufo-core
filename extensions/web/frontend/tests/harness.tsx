import { readFileSync } from "node:fs";
import { join } from "node:path";

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { expect, vi } from "vitest";

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

export function PlacedSection({ section }: { section: Section }) {
  const [placed, setPlaced] = useState<{ section: Section; place: WorkspacePlace }>({
    section,
    place: {},
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

/** A streaming reply is drawn word by word, so one line of prose lives in more than one element.
 *  `saying` matches the innermost element whose whole text is that line. */
export function saying(line: string | RegExp) {
  const matches = (text: string) => (typeof line === "string" ? text === line : line.test(text));
  return (_content: string, node: Element | null) =>
    node !== null &&
    matches(node.textContent ?? "") &&
    !Array.from(node.children).some((child) => matches(child.textContent ?? ""));
}

export function useStreamFake() {
  StreamFake.reset();
  vi.stubGlobal("EventSource", StreamFake);
}

export type Route = (url: string, init?: RequestInit) => Response | Promise<Response>;

export function wire(routes: Record<string, Route>) {
  const calls: string[] = [];
  const table: Record<string, Route> = {
    "/api/chats": () => json({ chats: [] }),
    ...routes,
  };
  const handler = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push(url);
    for (const [pattern, route] of Object.entries(table)) {
      if (url.includes(pattern)) return route(url, init);
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
};

export const MEMBER = { id: "m1", email: "member@example.com", admin: false };

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

export const NO_TASKS = "No scheduled task is visible to you.";
export const NO_TRIGGERS = "No source trigger is visible to you.";
export const NO_ARTIFACTS = "A file or site an agent makes in a conversation is listed here.";

export const TASK_KIND = {
  kind: "scheduled_task",
  fields: ["conversation", "mine", "next_run_at", "origin", "paused", "owner_email", "prompt"],
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

/** Press a record's own row, where the row itself is the control that opens it. */
export async function pressRow(name: string): Promise<void> {
  const row = (await screen.findAllByText(name)).map((node) => node.closest("tr")).find(Boolean);
  if (!row) throw new Error("no row named " + name);
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

/** Every table on a screen, by the floor it declares. */
export function tableFloors(): string[] {
  return [...document.querySelectorAll("table")].map((table) => table.style.minWidth);
}
