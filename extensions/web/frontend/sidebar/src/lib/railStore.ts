import { useSyncExternalStore } from "react";

import type { ToastState } from "@/components/ui/toast";
import { getJson, type IntentOutcome } from "@/lib/api";
import { RESTING_STATUS_MS, WORKING_STATUS_MS } from "@/lib/appStatusStore";
import type { ChatTurn } from "@/lib/chatStore";
import {
  bumpChat,
  turnedChat,
  heldAppsExpanded,
  heldPinned,
  heldRailShown,
  heldRailSort,
  heldSectionsShut,
  heldSidebar,
  holdAppsExpanded,
  holdPinned,
  holdRailShown,
  holdRailSort,
  holdSectionsShut,
  holdSidebar,
  chatRows,
  mergeChats,
  railRows,
  readChat,
  type ChatRow,
  type ChatsPayload,
  type ConversationsPayload,
  type RailShown,
  type RailSort,
} from "@/lib/rail";
import type { OwnedConversation } from "@/lib/types";

export type RailPhase = "loading" | "failed" | "ready";

/** A refusal the resolve answered, read where no record of the conversation is held. */
export type Sought =
  | { kind: "absent" }
  | { kind: "signed-out" }
  | { kind: "failed"; message: string };

export type RailState = {
  phase: RailPhase;
  rows: ChatRow[];
  sought: Readonly<Record<string, Sought>>;
  linked: Readonly<Record<string, OwnedConversation>>;
  fault: ToastState | null;
  cut: boolean;
  shown: RailShown;
  sort: RailSort;
  collapsed: boolean;
  pinned: string[] | null;
  appsExpanded: boolean;
  sectionsShut: string[];
};

function fresh(): RailState {
  return {
    phase: "loading",
    rows: [],
    sought: {},
    linked: {},
    fault: null,
    cut: false,
    shown: heldRailShown(),
    sort: heldRailSort(),
    collapsed: heldSidebar(),
    pinned: heldPinned(),
    appsExpanded: heldAppsExpanded(),
    sectionsShut: heldSectionsShut(),
  };
}

let state: RailState | null = null;
const listeners = new Set<() => void>();
type VisibilityState = { audience: string; member_email: string | null };
type VisibilityChange = VisibilityState & { previous: VisibilityState };
const visibilityChanges = new Map<string, VisibilityChange>();

function applyVisibility<Held extends VisibilityState>(conversationId: string, held: Held): Held {
  const change = visibilityChanges.get(conversationId);
  return change ? { ...held, audience: change.audience, member_email: change.member_email } : held;
}

function withVisibility(held: RailState, conversationId: string, next: VisibilityState): RailState {
  const linked = held.linked[conversationId];
  return {
    ...held,
    rows: held.rows.map((entry) =>
      entry.conversation_id === conversationId ? { ...entry, ...next } : entry,
    ),
    linked: linked ? { ...held.linked, [conversationId]: { ...linked, ...next } } : held.linked,
  };
}

export function railState(): RailState {
  state ??= fresh();
  return state;
}

function update(change: (held: RailState) => RailState): void {
  state = change(railState());
  for (const listener of listeners) listener();
}

export function useRail(): RailState {
  return useSyncExternalStore((listener) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }, railState);
}

/** A retry answering after the read it replaced would otherwise put the older rows back. */
let reads = 0;

/** Comfortably past what the sidebar can usefully show, stated so a workspace with thousands of
 *  conversations costs a bounded number of reads. */
const RAIL_ROWS_MAX = 300;

export function readRail(): void {
  const read = ++reads;
  update((held) => ({ ...held, phase: "loading" }));
  void walkRail(read).then(wakeRail);
}

/** Each page stands as it lands and the walk goes on behind it, so the rail is drawn on the first
 *  read rather than on the whole continuation. */
async function walkRail(read: number): Promise<void> {
  const gathered: ChatRow[] = [];
  let cursor = "";
  for (;;) {
    const params = new URLSearchParams({ order_by: "last_at", order: "desc" });
    if (cursor) params.set("cursor", cursor);
    const result = await getJson<ConversationsPayload>(
      "/objects/conversation?" + params.toString(),
    );
    if (read !== reads) return;
    if (!result.ok) {
      update((held) => ({
        ...held,
        phase: "failed" as const,
        ...(result.status === 401
          ? {}
          : { fault: { title: "Conversations did not refresh.", description: result.message } }),
      }));
      return;
    }
    gathered.push(
      ...chatRows(result.payload).map((row) => applyVisibility(row.conversation_id, row)),
    );
    update((held) => ({
      ...held,
      phase: "ready" as const,
      cut: result.payload.cut === true,
      rows: mergeChats(gathered, held.rows),
    }));
    cursor = result.payload.next_cursor ?? "";
    if (!cursor || gathered.length >= RAIL_ROWS_MAX) return;
  }
}

/** One page answers the whole rail's dots: a running turn's own updates put its conversation at
 *  the head of the `last_at` order. */
async function refreshRail(): Promise<void> {
  const read = reads;
  const params = new URLSearchParams({ order_by: "last_at", order: "desc" });
  const result = await getJson<ConversationsPayload>("/objects/conversation?" + params.toString());
  if (read !== reads || !result.ok) return;
  const fetched = chatRows(result.payload).map((row) => applyVisibility(row.conversation_id, row));
  update((held) => ({ ...held, rows: mergeChats(fetched, held.rows) }));
}

let ticking: number | null = null;

function poll(): void {
  if (ticking !== null || document.visibilityState === "hidden") return;
  const held = railState();
  const live = railRows(held.rows, held.shown, held.sort).some(
    (row) => row.turn === "running" || row.turn === "queued",
  );
  ticking = window.setTimeout(
    () => {
      ticking = null;
      void refreshRail().then(poll);
    },
    live ? WORKING_STATUS_MS : RESTING_STATUS_MS,
  );
}

function woken(): void {
  if (document.visibilityState === "hidden") {
    if (ticking !== null) window.clearTimeout(ticking);
    ticking = null;
    return;
  }
  poll();
}

/** The cadence is read off the drawn rows, so whatever lands rows, starts a turn or changes what is
 *  drawn measures the wait ahead of the next re-read again rather than leaving the one it set. */
function wakeRail(): void {
  if (ticking === null) return;
  window.clearTimeout(ticking);
  ticking = null;
  poll();
}

export function watchRail(): () => void {
  document.addEventListener("visibilitychange", woken);
  poll();
  return () => {
    document.removeEventListener("visibilitychange", woken);
    if (ticking !== null) window.clearTimeout(ticking);
    ticking = null;
  };
}

/** An outstanding read is not an outcome, so it is not state a screen draws from: a permalink whose
 *  read has not answered is loading, never unshared. */
const seeking = new Set<string>();

/** Every visit reads the conversation again, so its title line follows a rename or a change of
 *  audience made since the last one; the record held meanwhile stands until the answer lands. */
export function seekChat(conversationId: string): void {
  if (seeking.has(conversationId)) return;
  seeking.add(conversationId);
  void getJson<ChatsPayload>("/api/chats?conversation=" + conversationId).then((result) => {
    seeking.delete(conversationId);
    if (result.ok) {
      update((current) => ({
        ...current,
        linked: {
          ...current.linked,
          [conversationId]: applyVisibility(conversationId, result.payload.conversation),
        },
      }));
      return;
    }
    const outcome: Sought =
      result.status === 404
        ? { kind: "absent" }
        : result.status === 401
          ? { kind: "signed-out" }
          : { kind: "failed", message: result.message };
    update((current) => ({ ...current, sought: { ...current.sought, [conversationId]: outcome } }));
  });
}

/** The founding client holds every fact the listing row and the resolved projection would carry, so
 *  both stand at once and the conversation opens without a read of what it just wrote. */
export function railFounded(row: ChatRow, conversation: OwnedConversation): void {
  update((held) => ({
    ...held,
    rows: mergeChats(held.rows, [row]),
    linked: { ...held.linked, [conversation.id]: conversation },
  }));
  wakeRail();
}

export function changeRailVisibility(
  conversationId: string,
  audience: string,
  memberEmail: string | null,
): boolean {
  const held = railState();
  const current =
    held.linked[conversationId] ??
    held.rows.find((entry) => entry.conversation_id === conversationId);
  if (!current || visibilityChanges.has(conversationId) || current.audience === audience) {
    return false;
  }
  const next = { audience, member_email: memberEmail };
  visibilityChanges.set(conversationId, {
    ...next,
    previous: { audience: current.audience, member_email: current.member_email },
  });
  update((state) => withVisibility(state, conversationId, next));
  return true;
}

export function settleRailVisibility(conversationId: string, outcome: IntentOutcome): void {
  const change = visibilityChanges.get(conversationId);
  if (!change) return;
  visibilityChanges.delete(conversationId);
  update((held) => ({
    ...(outcome.applied ? held : withVisibility(held, conversationId, change.previous)),
    ...(outcome.applied || !outcome.message
      ? {}
      : {
          fault: {
            title: "Visibility did not change.",
            description: outcome.message,
          },
        }),
  }));
  readRail();
}

export function railRead(conversationId: string): void {
  update((held) => ({ ...held, rows: readChat(held.rows, conversationId) }));
}

export function railActivity(conversationId: string, turn: ChatTurn): void {
  update((held) => ({
    ...held,
    rows:
      turn === "running"
        ? bumpChat(held.rows, conversationId, new Date(), turn)
        : turnedChat(held.rows, conversationId, turn),
  }));
  wakeRail();
}

export function pickRailSort(sort: RailSort): void {
  holdRailSort(sort);
  update((held) => ({ ...held, sort }));
}

export function quietRail(): void {
  update((held) => ({ ...held, fault: null }));
}

export function pickRailShown(shown: RailShown): void {
  holdRailShown(shown);
  update((held) => ({ ...held, shown }));
  wakeRail();
}

export function foldSidebar(collapsed: boolean): void {
  holdSidebar(collapsed);
  update((held) => ({ ...held, collapsed }));
}

export function pickPinned(pinned: string[]): void {
  holdPinned(pinned);
  update((held) => ({ ...held, pinned }));
}

export function pickSectionShut(label: string, shut: boolean): void {
  const held = railState().sectionsShut.filter((name) => name !== label);
  const next = shut ? held.concat(label) : held;
  holdSectionsShut(next);
  update((state) => ({ ...state, sectionsShut: next }));
}

export function pickAppsExpanded(appsExpanded: boolean): void {
  holdAppsExpanded(appsExpanded);
  update((held) => ({ ...held, appsExpanded }));
}

export function resetRailStore(): void {
  state = null;
  reads = 0;
  seeking.clear();
  visibilityChanges.clear();
  if (ticking !== null) window.clearTimeout(ticking);
  ticking = null;
  document.removeEventListener("visibilitychange", woken);
}
