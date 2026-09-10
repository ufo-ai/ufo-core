import { useSyncExternalStore } from "react";

import type { ToastState } from "@/components/ui/toast";
import { getJson } from "@/lib/api";
import { isPortalChat } from "@/lib/audience";
import {
  bumpChat,
  heldAppsExpanded,
  heldPinned,
  heldRailShown,
  heldRailShut,
  heldSectionsShut,
  heldSidebar,
  holdAppsExpanded,
  holdPinned,
  holdRailShown,
  holdRailShut,
  holdSectionsShut,
  holdSidebar,
  chatRows,
  mergeChats,
  type ChatRow,
  type ChatsPayload,
  type ConversationsPayload,
  type RailShown,
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
  shown: RailShown;
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
    shown: heldRailShown(),
    shut: heldRailShut(),
    collapsed: heldSidebar(),
    pinned: heldPinned(),
    appsExpanded: heldAppsExpanded(),
    sectionsShut: heldSectionsShut(),
  };
}

let state: RailState | null = null;
const listeners = new Set<() => void>();

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
  void walkRail(read);
}

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
    gathered.push(...chatRows(result.payload));
    cursor = result.payload.next_cursor ?? "";
    if (!cursor || gathered.length >= RAIL_ROWS_MAX) break;
  }
  update((held) => ({
    ...held,
    phase: "ready" as const,
    rows: mergeChats(gathered, held.rows),
  }));
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
        ? { rows: mergeChats(current.rows, result.payload.chats) }
        : {}),
      ...(result.ok && result.payload.conversation
        ? { linked: { ...current.linked, [conversationId]: result.payload.conversation } }
        : {}),
    }));
  });
}

export function railFounded(row: ChatRow): void {
  update((held) => ({ ...held, rows: mergeChats(held.rows, [row]) }));
}

export function railActivity(conversationId: string): void {
  update((held) => ({ ...held, rows: bumpChat(held.rows, conversationId, new Date()) }));
}

export function quietRail(): void {
  update((held) => ({ ...held, fault: null }));
}

export function pickRailShown(shown: RailShown): void {
  holdRailShown(shown);
  update((held) => ({ ...held, shown }));
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
}
