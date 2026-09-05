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
  heldRailSort,
  heldSectionsShut,
  heldSidebar,
  holdAppsExpanded,
  holdPinned,
  holdRailShown,
  holdRailShut,
  holdRailSort,
  holdSectionsShut,
  holdSidebar,
  chatRows,
  mergeChats,
  type ChatRow,
  type ChatsPayload,
  type ConversationsPayload,
  type RailShown,
  type RailSort,
} from "@/lib/rail";
import type { OwnedConversation } from "@/lib/types";

/** The sidebar's own state, outside the shell: the rail of conversations, what a permalink to one
 *  outside it resolved to, and the choices the member has made about how the rail is drawn. It was
 *  ten pieces of `App` state feeding twenty-two props down two components, and every screen that
 *  reads the rail reads it here instead \u2014 the same shape `chatStore` gives a conversation.
 *
 *  A choice the member makes is written to this browser as it is taken, so the reads that seed the
 *  store are the reads a reload makes. */

export type RailPhase = "loading" | "failed" | "ready";

/** What a read for a conversation the rail does not carry answered. A permalink can name a
 *  conversation this account may not read, or one no read of theirs answers at all. */
export type Sought =
  | { kind: "answered" }
  | { kind: "signed-out" }
  | { kind: "failed"; message: string };

export type RailState = {
  phase: RailPhase;
  rows: ChatRow[];
  sought: Readonly<Record<string, Sought>>;
  /** A conversation another surface holds, read by permalink: it has no rail row to route by, so it
   *  is held under its own id with the agent it ran under. */
  linked: Readonly<Record<string, OwnedConversation>>;
  /** A read that failed where no control on the screen can correct it. */
  fault: ToastState | null;
  sort: RailSort;
  shown: RailShown;
  shut: string[] | null;
  collapsed: boolean;
  pinned: string[] | null;
  appsExpanded: boolean;
  /** The sections the member has folded shut, by name. */
  sectionsShut: string[];
};

/** The state a browser opens on: nothing read yet, and every choice as this browser holds it. Built
 *  on first read rather than at import, so the reads happen once the page is standing. */
function fresh(): RailState {
  return {
    phase: "loading",
    rows: [],
    sought: {},
    linked: {},
    fault: null,
    sort: heldRailSort(),
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

/** How many reads of the rail have been asked for. A retry answering after the read it replaced
 *  would otherwise put the older rows back. */
let reads = 0;

/** How many rows one rail read gathers before it stops walking. The listing pages, so a rail
 *  read follows the continuation until the walk is done or this many rows stand — comfortably
 *  past what the sidebar can usefully show, stated so a workspace with thousands of
 *  conversations costs a bounded number of reads. */
const RAIL_ROWS_MAX = 300;

/** Read the rail: walk the listing's continuation, gathering pages until the walk is done or
 *  `RAIL_ROWS_MAX` rows stand. The rows already standing are kept while it runs and kept if it
 *  fails: a member reading a conversation should not lose the list of them because one read did
 *  not answer. A refused session states nothing here, because sign-in answers it. */
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

/** The conversations a read is out for. An outstanding read is not an outcome, so it is not state a
 *  screen draws from: a permalink whose read has not answered is loading, never unshared. */
const seeking = new Set<string>();

/** Resolve a conversation the rail does not carry, which is how a permalink to one opens: the read
 *  answers with the conversation itself, and with whatever rail rows came with it. Asked once per
 *  conversation — an answer already held, a read already out, or a row already standing for it is
 *  the answer. */
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

/** A conversation a send has just founded, carried into the rail without waiting for its next
 *  read. */
export function railFounded(row: ChatRow): void {
  update((held) => ({ ...held, rows: mergeChats(held.rows, [row]) }));
}

/** A conversation that has just been spoken in moves to the top of the rail. */
export function railActivity(conversationId: string): void {
  update((held) => ({ ...held, rows: bumpChat(held.rows, conversationId, new Date()) }));
}

export function quietRail(): void {
  update((held) => ({ ...held, fault: null }));
}

export function pickRailSort(sort: RailSort): void {
  holdRailSort(sort);
  update((held) => ({ ...held, sort }));
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

/** Fold a section shut, or open it again. Held by name, so a section the sidebar has stopped
 *  drawing keeps its answer and governs nothing until it is drawn again. */
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
