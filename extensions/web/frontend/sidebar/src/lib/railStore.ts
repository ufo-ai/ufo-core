import { useSyncExternalStore } from "react";

import type { ToastState } from "@/components/ui/toast";
import { getJson, type IntentOutcome } from "@/lib/api";
import { RESTING_STATUS_MS, WORKING_STATUS_MS } from "@/lib/appStatusStore";
import { isPortalChat } from "@/lib/audience";
import type { ChatTurn } from "@/lib/chatStore";
import {
  bumpChat,
  turnedChat,
  heldAppsExpanded,
  heldPinned,
  heldRailShown,
  heldRailSort,
  heldRailShut,
  heldSectionsShut,
  heldSidebar,
  holdAppsExpanded,
  holdPinned,
  holdRailShown,
  holdRailSort,
  holdRailShut,
  holdSectionsShut,
  holdSidebar,
  chatRows,
  mergeChats,
  railGroups,
  readChat,
  type ChatRow,
  type ChatsPayload,
  type ConversationsPayload,
  type RailShown,
  type RailSort,
} from "@/lib/rail";
import type { OwnedConversation } from "@/lib/types";

export type RailPhase = "loading" | "failed" | "ready";

export type Sought =
  | { kind: "answered" }
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
  shut: string[] | null;
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
    shut: heldRailShut(),
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

function applyVisibility(row: ChatRow): ChatRow {
  const change = visibilityChanges.get(row.conversation_id);
  return change ? { ...row, audience: change.audience, member_email: change.member_email } : row;
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
    gathered.push(...chatRows(result.payload).map(applyVisibility));
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
  const fetched = chatRows(result.payload).map(applyVisibility);
  update((held) => ({ ...held, rows: mergeChats(fetched, held.rows) }));
}

let ticking: number | null = null;

function poll(): void {
  if (ticking !== null || document.visibilityState === "hidden") return;
  const held = railState();
  const live = railGroups(held.rows, held.shown, held.sort).some((group) =>
    group.rows.some((row) => row.turn === "running" || row.turn === "queued"),
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

export function seekChat(conversationId: string): void {
  const held = railState();
  if (seeking.has(conversationId) || conversationId in held.sought) return;
  if (held.rows.some((row) => row.conversation_id === conversationId && isPortalChat(row.surface))) {
    return;
  }
  seeking.add(conversationId);
  void getJson<ChatsPayload>("/api/chats?conversation=" + conversationId).then((result) => {
    seeking.delete(conversationId);
    const outcome: Sought = result.ok
      ? { kind: "answered" }
      : result.status === 401
        ? { kind: "signed-out" }
        : { kind: "failed", message: result.message };
    update((current) => ({
      ...current,
      sought: { ...current.sought, [conversationId]: outcome },
      ...(result.ok && result.payload.chats.length
        ? { rows: mergeChats(current.rows, result.payload.chats.map(applyVisibility)) }
        : {}),
      ...(result.ok && result.payload.conversation
        ? { linked: { ...current.linked, [conversationId]: result.payload.conversation } }
        : {}),
    }));
  });
}

export function railFounded(row: ChatRow): void {
  update((held) => ({ ...held, rows: mergeChats(held.rows, [row]) }));
  wakeRail();
}

export function changeRailVisibility(
  conversationId: string,
  audience: string,
  memberEmail: string | null,
): boolean {
  const row = railState().rows.find((entry) => entry.conversation_id === conversationId);
  if (!row || visibilityChanges.has(conversationId) || row.audience === audience) return false;
  visibilityChanges.set(conversationId, {
    audience,
    member_email: memberEmail,
    previous: { audience: row.audience, member_email: row.member_email },
  });
  update((held) => ({
    ...held,
    rows: held.rows.map((entry) =>
      entry.conversation_id === conversationId
        ? { ...entry, audience, member_email: memberEmail }
        : entry,
    ),
  }));
  return true;
}

export function settleRailVisibility(conversationId: string, outcome: IntentOutcome): void {
  const change = visibilityChanges.get(conversationId);
  if (!change) return;
  visibilityChanges.delete(conversationId);
  update((held) => ({
    ...held,
    rows: outcome.applied
      ? held.rows
      : held.rows.map((entry) =>
          entry.conversation_id === conversationId
            ? {
                ...entry,
                audience: change.previous.audience,
                member_email: change.previous.member_email,
              }
            : entry,
        ),
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

export function pickRailShut(shut: string[]): void {
  holdRailShut(shut);
  update((held) => ({ ...held, shut }));
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
