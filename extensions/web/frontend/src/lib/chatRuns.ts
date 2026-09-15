import { useSyncExternalStore } from "react";

import { agentName } from "@/lib/agentName";
import { IMESSAGE_SURFACE, SLACK_SURFACE, UFO_SURFACE, isPortalChat } from "@/lib/audience";
import { heldStorage, type ChatRow } from "@/lib/rail";

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

export function heldChatLadder(): ChatLadder {
  return heldStorage()?.getItem(HELD_LADDER) === "app" ? "app" : "recency";
}

export function holdChatLadder(ladder: ChatLadder): void {
  heldStorage()?.setItem(HELD_LADDER, ladder);
  told();
}

let heldWord = "";
let heldSurfaces: string[] = [];

export function heldChatHidden(): string[] {
  const word = heldStorage()?.getItem(HELD_HIDDEN) ?? "";
  if (word !== heldWord) {
    heldWord = word;
    heldSurfaces = word.split(",").filter(Boolean);
  }
  return heldSurfaces;
}

export function holdChatHidden(hidden: string[]): void {
  heldStorage()?.setItem(HELD_HIDDEN, hidden.join(","));
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
