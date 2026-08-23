import { agentName } from "@/lib/agentName";
import { IMESSAGE_SURFACE, SLACK_SURFACE, UFO_SURFACE } from "@/lib/audience";
import type { OwnedConversation } from "@/lib/types";

export type ChatRow = {
  conversation_id: string;
  agent_id: string;
  agent_name: string;
  agent_model?: string;
  title: string;
  last_at: string;
  surface: string;
  surface_label: string | null;
  mine: boolean;
  speaker: string | null;
};

/** The rail, and — for a permalink to a conversation another surface holds, which has no chat row
 *  to route by — that conversation, naming the agent it ran under. */
export type ChatsPayload = { chats: ChatRow[]; conversation?: OwnedConversation };

export type RailGroup = { label: string; rows: ChatRow[] };

const GROUPS = ["Today", "Yesterday", "Previous 7 days", "Previous 30 days", "Older"] as const;

const DAY_MS = 86_400_000;

function groupLabel(raw: string, now: Date): (typeof GROUPS)[number] {
  const at = new Date(raw);
  if (Number.isNaN(at.getTime())) return "Older";
  const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  if (at.getTime() >= midnight) return "Today";
  if (at.getTime() >= midnight - DAY_MS) return "Yesterday";
  if (at.getTime() >= midnight - 7 * DAY_MS) return "Previous 7 days";
  if (at.getTime() >= midnight - 30 * DAY_MS) return "Previous 30 days";
  return "Older";
}

function groupChats(rows: ChatRow[], now: Date): RailGroup[] {
  const buckets = new Map<string, ChatRow[]>();
  for (const row of rows) {
    const label = groupLabel(row.last_at, now);
    buckets.set(label, (buckets.get(label) ?? []).concat(row));
  }
  return GROUPS.filter((label) => buckets.has(label)).map((label) => ({
    label,
    rows: buckets.get(label) ?? [],
  }));
}

export type RailSort = "recency" | "agent";

const HELD_SORT = "rail-sort";

/** Which ladder the rail draws its rows in, held across sessions. */
export function heldRailSort(): RailSort {
  return localStorage.getItem(HELD_SORT) === "agent" ? "agent" : "recency";
}

export function holdRailSort(sort: RailSort): void {
  localStorage.setItem(HELD_SORT, sort);
}

/** The agent sort's groups. Rows bucket under the name as stored — that name is the agent's
 *  identity, and two agents whose names differ only in case are two agents — and the heading is
 *  that name drawn the way every other surface draws it. */
function groupChatsByAgent(rows: ChatRow[]): RailGroup[] {
  const buckets = new Map<string, ChatRow[]>();
  for (const row of rows) {
    buckets.set(row.agent_name, (buckets.get(row.agent_name) ?? []).concat(row));
  }
  return [...buckets.entries()].map(([name, grouped]) => ({
    label: agentName(name),
    rows: grouped,
  }));
}

export type RailShown = { terminal: boolean; slack: boolean; imessage: boolean };

export const RAIL_SHOWN_OPTIONS: { surface: keyof RailShown; label: string }[] = [
  { surface: "terminal", label: "Terminal" },
  { surface: "slack", label: "Slack" },
  { surface: "imessage", label: "iMessage" },
];

const HELD_SHOWN = "rail-shown";

/** Which surfaces beside the portal's own the rail admits, held across sessions. A Slack thread, a
 *  terminal session and an iMessage exchange are conversations the member had somewhere else, so
 *  each is admitted only once they name it, and a browser holding nothing admits none. */
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

/** The groups the member has shut, by label, held across sessions — a rail narrowed to the ladders
 *  someone works from would widen again on every reload otherwise. A label is the key under either
 *  sort, since an agent's name and a date bucket's name are both labels. A browser holding nothing
 *  has never had a heading clicked, which is not the same as one holding an empty set. */
export function heldRailShut(): string[] | null {
  const held = localStorage.getItem(HELD_SHUT);
  return held === null ? null : held.split("\n").filter(Boolean);
}

export function holdRailShut(shut: string[]): void {
  localStorage.setItem(HELD_SHUT, shut.join("\n"));
}

/** Which groups stand shut. Until the member has touched a heading the rail opens its first group
 *  and shuts the rest: the conversations someone is working from are the recent ones, and the rest
 *  of the history is a list they ask for. The first click writes that whole set down, so from then
 *  on the member's own set governs — including the set that holds nothing shut. A label the rail
 *  has stopped drawing stays in the set and governs nothing until it is drawn again. */
export function railShut(held: string[] | null, labels: string[]): string[] {
  return held ?? labels.slice(1);
}

const HELD_SIDEBAR = "sidebar";

/** Whether the sidebar stands folded to its glyph rail. The shell opens on the rail: the sidebar is
 *  a place a member goes to find a conversation by name, not the screen they came for, so the width
 *  it takes belongs to the screen until they ask for it — and once they have asked, that choice is
 *  theirs on every load after. */
export function heldSidebar(): boolean {
  return localStorage.getItem(HELD_SIDEBAR) !== "expanded";
}

export function holdSidebar(collapsed: boolean): void {
  localStorage.setItem(HELD_SIDEBAR, collapsed ? "collapsed" : "expanded");
}

const HELD_PINNED = "pinned-rows";

/** What the member pinned into the sidebar — apps by id, in the order they pinned them. A browser
 *  holding nothing (`null`) has never had a pin touched, which is not the same as one holding an
 *  empty set: until then the workspace's shipped apps stand pinned, so the sidebar arrives holding
 *  its own destinations. A stored id no live agent answers — an app since removed — resolves to
 *  nothing rather than a row. */
export function heldPinned(): string[] | null {
  const held = localStorage.getItem(HELD_PINNED);
  return held === null ? null : held.split("\n").filter(Boolean);
}

export function holdPinned(pinned: string[]): void {
  localStorage.setItem(HELD_PINNED, pinned.join("\n"));
}

function admits(row: ChatRow, shown: RailShown): boolean {
  if (row.surface === UFO_SURFACE) return shown.terminal;
  if (row.surface === SLACK_SURFACE) return shown.slack;
  if (row.surface === IMESSAGE_SURFACE) return shown.imessage;
  return true;
}

const OTHER_MEMBERS = "Other members";

/** The rail's groups, in the order it draws them. The member's own conversations take the ladder
 *  the sort names, and the readable ones their colleagues are in follow as one group at the foot —
 *  never subdivided, and by recency under either sort: a sidebar column holds two heading weights,
 *  not three, and a colleague's thread is read for what happened lately in it. The group is drawn
 *  only when it holds a row.
 *
 *  The filter narrows the groups and never the rail itself: a permalink to a Slack thread opens it
 *  whether or not the rail is admitting Slack. */
export function railGroups(
  rows: ChatRow[],
  sort: RailSort,
  shown: RailShown,
  now: Date,
): RailGroup[] {
  const admitted = rows.filter((row) => admits(row, shown));
  const own = admitted.filter((row) => row.mine);
  const theirs = admitted.filter((row) => !row.mine);
  const grouped = sort === "agent" ? groupChatsByAgent(own) : groupChats(own, now);
  return theirs.length ? grouped.concat({ label: OTHER_MEMBERS, rows: theirs }) : grouped;
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
