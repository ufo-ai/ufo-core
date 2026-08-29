import { useSyncExternalStore } from "react";

import { agentName } from "@/lib/agentName";
import { IMESSAGE_SURFACE, SLACK_SURFACE, UFO_SURFACE, isPortalChat } from "@/lib/audience";
import type { Agent, OwnedConversation } from "@/lib/types";

export type ChatRow = {
  conversation_id: string;
  agent_id: string;
  agent_name: string;
  title: string;
  last_at: string;
  surface: string;
  surface_label: string | null;
  mine: boolean;
  speaker: string | null;
};

/** The permalink resolve, and — for a conversation another surface holds, which has no chat row
 *  to route by — that conversation, naming the agent it ran under. */
export type ChatsPayload = { chats: ChatRow[]; conversation?: OwnedConversation };

/** One row of the conversation kind's member listing — the rail's read. The row's `name` is the
 *  conversation id, and the index adds the agent beside the kind's own fields. */
export type ConversationRow = {
  name: string;
  agent_id: string;
  agent_name: string;
  title: string;
  last_at: string;
  surface: string;
  surface_label: string | null;
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
    mine: row.mine,
    speaker: row.speaker,
  }));
}

/** The surfaces the member can put away. A Slack thread, a terminal session and an iMessage exchange
 *  are conversations they had somewhere else, and they are still their conversations — so the history
 *  holds every one of them until it is asked not to. */
export const CHAT_SHOWN_OPTIONS: { surface: string; label: string }[] = [
  { surface: UFO_SURFACE, label: "Terminal" },
  { surface: SLACK_SURFACE, label: "Slack" },
  { surface: IMESSAGE_SURFACE, label: "iMessage" },
];

/** Which ladder the history runs its rows in. Recency is the one it opens on. */
export type ChatLadder = "recency" | "app";

export const CHAT_LADDERS: { value: ChatLadder; label: string }[] = [
  { value: "recency", label: "Recency" },
  { value: "app", label: "App" },
];

const HELD_LADDER = "chat-ladder";
const HELD_HIDDEN = "chat-hidden";

/** The ladder and the put-away surfaces are standing choices rather than places: a member who asked
 *  to see their terminal sessions asked about their own history, and a list that forgot on the way to
 *  another screen and back would ask them again every visit.
 *
 *  A browser holding nothing has put nothing away, which is every surface drawn.
 *
 *  Both picks belong to the browser rather than to one list drawn on it, so every history standing on
 *  the screen reads the one answer and is told the moment it changes: a member who narrows the
 *  history in one lane has narrowed their history, and the lane beside it says so unopened. */
export function heldChatLadder(): ChatLadder {
  return localStorage.getItem(HELD_LADDER) === "app" ? "app" : "recency";
}

export function holdChatLadder(ladder: ChatLadder): void {
  localStorage.setItem(HELD_LADDER, ladder);
  told();
}

let heldWord = "";
let heldSurfaces: string[] = [];

/** The surfaces put away, as one array per stored word: a store hands the same snapshot back until
 *  the pick itself changes, and a fresh array on every read is a change to a reader. */
export function heldChatHidden(): string[] {
  const word = localStorage.getItem(HELD_HIDDEN) ?? "";
  if (word !== heldWord) {
    heldWord = word;
    heldSurfaces = word.split(",").filter(Boolean);
  }
  return heldSurfaces;
}

export function holdChatHidden(hidden: string[]): void {
  localStorage.setItem(HELD_HIDDEN, hidden.join(","));
  told();
}

const listeners = new Set<() => void>();

function told(): void {
  for (const listener of listeners) listener();
}

/** Another tab of the portal writes the same browser store, and it says so with a `storage` event
 *  rather than through this page's own writes, so the listener stands while anything is reading. */
function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (listeners.size === 1) globalThis.addEventListener("storage", told);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) globalThis.removeEventListener("storage", told);
  };
}

/** Which ladder this browser runs the history in. */
export function useChatLadder(): ChatLadder {
  return useSyncExternalStore(subscribe, heldChatLadder);
}

/** The surfaces this browser has put away. */
export function useChatHidden(): string[] {
  return useSyncExternalStore(subscribe, heldChatHidden);
}

/** Whether a row's surface is drawn. Everything is, bar the surfaces the member has put away. A
 *  portal chat is never put away: the history is the portal's own. */
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

/** Which calendar day a stamp fell on, in UTC as it was sent — the same UTC every other stamp on
 *  this surface reads in, so no reader's zone moves a conversation across midnight. */
function dayOf(at: Date): number {
  return Date.UTC(at.getUTCFullYear(), at.getUTCMonth(), at.getUTCDate()) / DAY_MS;
}

/** Which day-run a stamp falls in, counted in whole calendar days rather than in elapsed hours: a
 *  conversation at one this morning and one at eleven last night are two days apart to a reader and
 *  two hours apart to a clock, and it is the reader the headings are for. Today and Yesterday are
 *  carved out first, so the runs below them hold the days they have left. A stamp ahead of now — a
 *  clock askew between two machines — reads as today rather than as a run of its own. */
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

/** One run of rows the history draws together, under the heading its ladder named. */
export type ChatRun = { label: string; rows: ChatRow[] };

export const OTHER_MEMBERS = "Other members";

/** The runs the history draws, in order. The member's own conversations take the ladder — a date run
 *  or the app each ran under — and the readable ones their colleagues are in follow as one run at the
 *  foot, never subdivided and by recency under either ladder: a colleague's thread is read for what
 *  happened lately in it. A run is drawn only where it holds a row, and the rows inside one keep the
 *  recency the read handed them.
 *
 *  The date runs are named in a fixed order rather than the order their rows arrive in, so a week with
 *  nothing in it does not reorder the list. The app ladder takes the order its first row appeared in,
 *  which under a recency read is the app that spoke last. */
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

/** The apps in the order the sidebar draws them: the pinned ones in the order the member pinned
 *  them, then every other app by name. A pin is a place the member put a row, so it holds that place
 *  whatever the app has been doing since; below the pins the column is alphabetical, which is the
 *  one order a member can predict — they know the app's name before they know when it last ran, and
 *  a column that reordered itself as apps worked moved the row they were reaching for.
 *
 *  A stored pin no live app answers — an app since removed — resolves to nothing rather than a row.
 *
 *  Every app the workspace has is drawn. The list scrolls inside the height `--size-apps-open`
 *  allows, so a long one costs the column nothing — and an app the list refused to draw is one the
 *  member has no way to reach, which is what a pin they cannot see cannot be undone from. */
export function appOrder(apps: Agent[], pinned: string[]): Agent[] {
  const byId = new Map(apps.map((app) => [app.id, app]));
  const stood = pinned.map((id) => byId.get(id)).filter((app) => app !== undefined);
  const drawn = new Set(stood);
  const rest = apps.filter((app) => !drawn.has(app));
  rest.sort((left, right) => left.name.localeCompare(right.name));
  return stood.concat(rest);
}

const HELD_SECTIONS_SHUT = "sections-shut";

/** The sidebar sections the member has folded shut, by name, held across sessions. A section is a
 *  place they keep or put away; one they put away stays away on the next load, because a column
 *  narrowed to the part someone works from would widen again on every reload otherwise. A browser
 *  holding nothing has folded none, which is the sidebar whole. */
export function heldSectionsShut(): string[] {
  return (localStorage.getItem(HELD_SECTIONS_SHUT) ?? "").split("\n").filter(Boolean);
}

export function holdSectionsShut(shut: string[]): void {
  localStorage.setItem(HELD_SECTIONS_SHUT, shut.join("\n"));
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
