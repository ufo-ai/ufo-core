import type { Conversation } from "@/lib/types";

export type ChatRow = {
  conversation_id: string;
  agent_id: string;
  agent_name: string;
  title: string;
  last_at: string;
};

export type LinkedConversation = Conversation & { agent_id: string };

export type ChatsPayload = { chats: ChatRow[]; conversation?: LinkedConversation };

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

export function groupChats(rows: ChatRow[], now: Date): RailGroup[] {
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

export function relativeTime(raw: string, now: Date): string {
  const at = new Date(raw);
  if (Number.isNaN(at.getTime())) return "";
  const elapsed = now.getTime() - at.getTime();
  if (elapsed < 60_000) return "now";
  if (elapsed < 3_600_000) return Math.floor(elapsed / 60_000) + "m";
  if (elapsed < DAY_MS) return Math.floor(elapsed / 3_600_000) + "h";
  if (elapsed < 30 * DAY_MS) return Math.floor(elapsed / DAY_MS) + "d";
  return at.toLocaleDateString();
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
