import { SLACK_SURFACE, UFO_SURFACE } from "@/lib/audience";
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

function groupChatsByAgent(rows: ChatRow[]): RailGroup[] {
  const buckets = new Map<string, ChatRow[]>();
  for (const row of rows) {
    buckets.set(row.agent_name, (buckets.get(row.agent_name) ?? []).concat(row));
  }
  return [...buckets.entries()].map(([label, grouped]) => ({ label, rows: grouped }));
}

export type RailShown = { terminal: boolean; slack: boolean };

export const RAIL_SHOWN_OPTIONS: { surface: keyof RailShown; label: string }[] = [
  { surface: "terminal", label: "Terminal" },
  { surface: "slack", label: "Slack" },
];

const HELD_SHOWN = "rail-shown";

/** Which surfaces beside the portal's own the rail admits, held across sessions. A Slack thread or
 *  a terminal session is a conversation the member had somewhere else, so each is admitted only
 *  once they name it, and a browser holding nothing admits neither. */
export function heldRailShown(): RailShown {
  const held = (localStorage.getItem(HELD_SHOWN) ?? "").split(",");
  return { terminal: held.includes("terminal"), slack: held.includes("slack") };
}

export function holdRailShown(shown: RailShown): void {
  const named = RAIL_SHOWN_OPTIONS.filter((option) => shown[option.surface]);
  localStorage.setItem(HELD_SHOWN, named.map((option) => option.surface).join(","));
}

function admits(row: ChatRow, shown: RailShown): boolean {
  if (row.surface === UFO_SURFACE) return shown.terminal;
  if (row.surface === SLACK_SURFACE) return shown.slack;
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
