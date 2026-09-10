import { useSyncExternalStore } from "react";

import { agentName } from "@/lib/agentName";
import {
  IMESSAGE_SURFACE,
  SLACK_SURFACE,
  UFO_SURFACE,
  isPortalChat,
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

export const CHAT_SHOWN_OPTIONS: { surface: string; label: string }[] = [
  { surface: UFO_SURFACE, label: "Terminal" },
  { surface: SLACK_SURFACE, label: "Slack" },
  { surface: IMESSAGE_SURFACE, label: "iMessage" },
];

export type ChatLadder = "recency" | "app";

export const CHAT_LADDERS: { value: ChatLadder; label: string }[] = [
  { value: "recency", label: "Recency" },
  { value: "app", label: "App" },
];

const HELD_LADDER = "chat-ladder";
const HELD_HIDDEN = "chat-hidden";

/** An app page frame reaches this module, and a browser blocking third-party storage raises on the
 *  property itself rather than on the read. */
function store(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

export function heldChatLadder(): ChatLadder {
  return store()?.getItem(HELD_LADDER) === "app" ? "app" : "recency";
}

export function holdChatLadder(ladder: ChatLadder): void {
  store()?.setItem(HELD_LADDER, ladder);
  told();
}

let heldWord = "";
let heldSurfaces: string[] = [];

export function heldChatHidden(): string[] {
  const word = store()?.getItem(HELD_HIDDEN) ?? "";
  if (word !== heldWord) {
    heldWord = word;
    heldSurfaces = word.split(",").filter(Boolean);
  }
  return heldSurfaces;
}

export function holdChatHidden(hidden: string[]): void {
  store()?.setItem(HELD_HIDDEN, hidden.join(","));
  told();
}

const listeners = new Set<() => void>();

function told(): void {
  for (const listener of listeners) listener();
}

/** Another tab of the portal writes the same browser store, and it says so with a `storage` event
 *  rather than through this page's own writes. */
function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (listeners.size === 1) globalThis.addEventListener("storage", told);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) globalThis.removeEventListener("storage", told);
  };
}

export function useChatLadder(): ChatLadder {
  return useSyncExternalStore(subscribe, heldChatLadder);
}

export function useChatHidden(): string[] {
  return useSyncExternalStore(subscribe, heldChatHidden);
}

export function chatShown(row: ChatRow, hidden: string[]): boolean {
  return isPortalChat(row.surface) || !hidden.includes(row.surface);
}

const DAY_MS = 86_400_000;

export const CHAT_DATE_RUNS = [
  "Today",
  "Yesterday",
  "Previous 7 days",
  "Previous 30 days",
  "Older",
];

function dayOf(at: Date): number {
  return Date.UTC(at.getUTCFullYear(), at.getUTCMonth(), at.getUTCDate()) / DAY_MS;
}

export function chatDateRun(raw: string, now: Date): string {
  const at = new Date(raw);
  if (Number.isNaN(at.getTime())) return "Older";
  const days = dayOf(now) - dayOf(at);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return "Previous 7 days";
  if (days < 30) return "Previous 30 days";
  return "Older";
}

export type ChatRun = { label: string; rows: ChatRow[] };

export const OTHER_MEMBERS = "Other members";

export function chatRuns(
  rows: ChatRow[],
  ladder: ChatLadder,
  hidden: string[],
  now: Date,
): ChatRun[] {
  const shown = rows.filter((row) => chatShown(row, hidden));
  const own = shown.filter((row) => row.mine);
  const theirs = shown.filter((row) => !row.mine);
  const grouped = own.length ? bucketed(own, ladder, now) : [];
  return theirs.length ? grouped.concat({ label: OTHER_MEMBERS, rows: theirs }) : grouped;
}

function bucketed(rows: ChatRow[], ladder: ChatLadder, now: Date): ChatRun[] {
  const named = (row: ChatRow) =>
    ladder === "app" ? agentName(row.agent_name) : chatDateRun(row.last_at, now);
  const buckets = new Map<string, ChatRow[]>();
  for (const row of rows) {
    const label = named(row);
    buckets.set(label, (buckets.get(label) ?? []).concat(row));
  }
  const order =
    ladder === "recency" ? CHAT_DATE_RUNS.filter((run) => buckets.has(run)) : [...buckets.keys()];
  return order.map((run) => ({ label: run, rows: buckets.get(run) ?? [] }));
}

const HELD_PINNED = "pinned-rows";

/** A browser holding `null` has never had a pin touched, which is not the same as one holding an empty
 *  set. A stored id no live agent answers resolves to nothing rather than a row. */
export function heldPinned(): string[] | null {
  const held = store()?.getItem(HELD_PINNED) ?? null;
  return held === null ? null : held.split("\n").filter(Boolean);
}

export function holdPinned(pinned: string[]): void {
  store()?.setItem(HELD_PINNED, pinned.join("\n"));
}

export function appOrder(apps: Agent[], pinned: string[]): Agent[] {
  const byId = new Map(apps.map((app) => [app.id, app]));
  const stood = pinned.map((id) => byId.get(id)).filter((app) => app !== undefined);
  const drawn = new Set(stood);
  const rest = apps.filter((app) => !drawn.has(app));
  rest.sort((left, right) => left.name.localeCompare(right.name));
  return stood.concat(rest);
}

const HELD_SECTIONS_SHUT = "sections-shut";

export function heldSectionsShut(): string[] {
  return (store()?.getItem(HELD_SECTIONS_SHUT) ?? "").split("\n").filter(Boolean);
}

export function holdSectionsShut(shut: string[]): void {
  store()?.setItem(HELD_SECTIONS_SHUT, shut.join("\n"));
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
