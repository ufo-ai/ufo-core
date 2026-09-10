import {
  IMESSAGE_SURFACE,
  SLACK_SURFACE,
  UFO_SURFACE,
  type AudienceEntry,
} from "@/lib/audience";
import type { Agent, OwnedConversation } from "@/lib/types";

export type ChatRow = {
  conversation_id: string;
  agent_id: string;
  agent_name: string;
  title: string;
  last_at: string;
  surface: string;
  surface_label: string | null;
  audience: string;
  member_email: string | null;
  mine: boolean;
  speaker: string | null;
};

export type ChatsPayload = { chats: ChatRow[]; conversation?: OwnedConversation };

export type ConversationRow = {
  name: string;
  agent_id: string;
  agent_name: string;
  title: string;
  last_at: string;
  surface: string;
  surface_label: string | null;
  audience: string;
  member_email: string | null;
  mine: boolean;
  speaker: string | null;
};

export type ConversationsPayload = { objects: ConversationRow[]; next_cursor?: string | null };

export function chatRows(payload: ConversationsPayload): ChatRow[] {
  return payload.objects.map((row) => ({
    conversation_id: row.name,
    agent_id: row.agent_id,
    agent_name: row.agent_name,
    title: row.title,
    last_at: row.last_at,
    surface: row.surface,
    surface_label: row.surface_label,
    audience: row.audience,
    member_email: row.member_email,
    mine: row.mine,
    speaker: row.speaker,
  }));
}

/** Who reads one conversation, from whichever rail record names it: the listing row for a chat
 *  the portal carries, the resolved projection for one another surface holds. A conversation the
 *  rail has not answered for yet has no audience to state, and the pane states none. */
export function railAudience(
  rows: ChatRow[],
  linked: Readonly<Record<string, OwnedConversation>>,
  conversationId: string,
): AudienceEntry | undefined {
  return rows.find((row) => row.conversation_id === conversationId) ?? linked[conversationId];
}

export type RailGroup = { label: string | null; rows: ChatRow[] };

export type RailShown = { terminal: boolean; slack: boolean; imessage: boolean };

export const RAIL_SHOWN_OPTIONS: { surface: keyof RailShown; label: string }[] = [
  { surface: "terminal", label: "Terminal" },
  { surface: "slack", label: "Slack" },
  { surface: "imessage", label: "iMessage" },
];

const HELD_SHOWN = "rail-shown";

export function heldRailShown(): RailShown {
  const held = (localStorage.getItem(HELD_SHOWN) ?? "").split(",");
  return {
    terminal: held.includes("terminal"),
    slack: held.includes("slack"),
    imessage: held.includes("imessage"),
  };
}

export function holdRailShown(shown: RailShown): void {
  const named = RAIL_SHOWN_OPTIONS.filter((option) => shown[option.surface]);
  localStorage.setItem(HELD_SHOWN, named.map((option) => option.surface).join(","));
}

const HELD_SHUT = "rail-shut";

/** A browser holding nothing has never had a heading clicked, which is not the same as one holding an
 *  empty set. */
export function heldRailShut(): string[] | null {
  const held = localStorage.getItem(HELD_SHUT);
  return held === null ? null : held.split("\n").filter(Boolean);
}

export function holdRailShut(shut: string[]): void {
  localStorage.setItem(HELD_SHUT, shut.join("\n"));
}

export function railShut(held: string[] | null, labels: (string | null)[]): string[] {
  return held ?? labels.slice(1).filter((label) => label !== null);
}

const HELD_SIDEBAR = "sidebar";

export function heldSidebar(): boolean {
  return localStorage.getItem(HELD_SIDEBAR) === "collapsed";
}

export function holdSidebar(collapsed: boolean): void {
  localStorage.setItem(HELD_SIDEBAR, collapsed ? "collapsed" : "expanded");
}

const HELD_PINNED = "pinned-rows";

/** A browser holding `null` has never had a pin touched, which is not the same as one holding an empty
 *  set. A stored id no live agent answers resolves to nothing rather than a row. */
export function heldPinned(): string[] | null {
  const held = localStorage.getItem(HELD_PINNED);
  return held === null ? null : held.split("\n").filter(Boolean);
}

export function holdPinned(pinned: string[]): void {
  localStorage.setItem(HELD_PINNED, pinned.join("\n"));
}

/** Two apps sharing a moment keep the order they arrived in, so a read answering for neither settles
 *  the column rather than shuffling it. */
export function appOrder(
  apps: Agent[],
  pinned: string[],
  lastActiveAt: (agentId: string) => number | null,
): Agent[] {
  const byId = new Map(apps.map((app) => [app.id, app]));
  const stood = pinned.map((id) => byId.get(id)).filter((app) => app !== undefined);
  const drawn = new Set(stood);
  const active: { app: Agent; at: number }[] = [];
  const never: Agent[] = [];
  for (const app of apps) {
    if (drawn.has(app)) continue;
    const at = lastActiveAt(app.id);
    if (at === null) never.push(app);
    else active.push({ app, at });
  }
  active.sort((left, right) => right.at - left.at);
  never.sort((left, right) => left.name.localeCompare(right.name));
  return stood.concat(
    active.map((row) => row.app),
    never,
  );
}

export type AppRun = { shown: Agent[]; more: Agent[] };

/** Opened, the list scrolls inside `--size-apps-open` rather than cutting its tail: an app the list
 *  refused to draw is one the member has no way to reach. */
const APPS_SHOWN = 8;

/** The cut is a count, not a judgement about any one app: a run that admitted apps for being busy
 *  would resize itself as work started, and the list would collapse under the member as they acted. */
export function appRun(apps: Agent[], working: (agentId: string) => boolean): AppRun {
  const busy = apps.filter((app) => working(app.id));
  const risen = new Set(busy.map((app) => app.id));
  const ladder = busy.concat(apps.filter((app) => !risen.has(app.id)));
  return { shown: ladder.slice(0, APPS_SHOWN), more: ladder.slice(APPS_SHOWN) };
}

const HELD_SECTIONS_SHUT = "sections-shut";

export function heldSectionsShut(): string[] {
  return (localStorage.getItem(HELD_SECTIONS_SHUT) ?? "").split("\n").filter(Boolean);
}

export function holdSectionsShut(shut: string[]): void {
  localStorage.setItem(HELD_SECTIONS_SHUT, shut.join("\n"));
}

const HELD_APPS_EXPANDED = "apps-expanded";

export function heldAppsExpanded(): boolean {
  return localStorage.getItem(HELD_APPS_EXPANDED) === "expanded";
}

export function holdAppsExpanded(expanded: boolean): void {
  localStorage.setItem(HELD_APPS_EXPANDED, expanded ? "expanded" : "collapsed");
}

function admits(row: ChatRow, shown: RailShown): boolean {
  if (row.surface === UFO_SURFACE) return shown.terminal;
  if (row.surface === SLACK_SURFACE) return shown.slack;
  if (row.surface === IMESSAGE_SURFACE) return shown.imessage;
  return true;
}

const OTHER_MEMBERS = "Other members";

/** The rail stands in one order, newest first: a chat is found by when it last moved. */
export function railGroups(rows: ChatRow[], shown: RailShown): RailGroup[] {
  const admitted = rows.filter((row) => admits(row, shown));
  const own = admitted.filter((row) => row.mine);
  const theirs = admitted.filter((row) => !row.mine);
  const grouped: RailGroup[] = own.length ? [{ label: null, rows: own }] : [];
  return theirs.length ? grouped.concat({ label: OTHER_MEMBERS, rows: theirs }) : grouped;
}

const MINUTE_MS = 60_000;
const HOUR_MS = 3_600_000;
const DAY_MS = 86_400_000;
const WEEK_MS = 604_800_000;
const YEAR_MS = 31_536_000_000;

/** The rail's own stamp, narrower than the `Moment` the rest of the portal draws: the sidebar's
 *  width belongs to the titles, so a row spends one number and one letter on the age of its chat.
 *  The whole moment is still the row's to state, and the `time` it sits in carries it. A stamp
 *  ahead of now — a clock that disagrees with the server's — reads as `now` rather than as a
 *  negative age. */
export function railStamp(raw: string, now: Date): string {
  const at = new Date(raw).getTime();
  if (Number.isNaN(at)) return "";
  const span = Math.max(0, now.getTime() - at);
  if (span < MINUTE_MS) return "now";
  if (span < HOUR_MS) return Math.floor(span / MINUTE_MS) + "m";
  if (span < DAY_MS) return Math.floor(span / HOUR_MS) + "h";
  if (span < WEEK_MS) return Math.floor(span / DAY_MS) + "d";
  if (span < YEAR_MS) return Math.floor(span / WEEK_MS) + "w";
  return Math.floor(span / YEAR_MS) + "y";
}

export function stampIso(at: Date): string {
  return at.toISOString();
}

function moment(row: ChatRow): number {
  const at = new Date(row.last_at).getTime();
  return Number.isNaN(at) ? 0 : at;
}

export function bumpChat(rows: ChatRow[], conversationId: string, at: Date): ChatRow[] {
  const bumped = rows.map((row) =>
    row.conversation_id === conversationId ? { ...row, last_at: stampIso(at) } : row,
  );
  bumped.sort((a, b) => moment(b) - moment(a));
  return bumped;
}

export function mergeChats(fetched: ChatRow[], held: ChatRow[]): ChatRow[] {
  const known = new Set(fetched.map((row) => row.conversation_id));
  const merged = fetched.concat(held.filter((row) => !known.has(row.conversation_id)));
  merged.sort((a, b) => moment(b) - moment(a));
  return merged;
}
