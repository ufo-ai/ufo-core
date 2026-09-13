import { useSyncExternalStore } from "react";

import type { ToastState } from "@/components/ui/toast";
import { getJson, type IntentOutcome } from "@/lib/api";
import { isPortalChat } from "@/lib/audience";
import type { ChatTurn } from "@/lib/chatStore";
import {
  bumpChat,
  heldPinned,
  heldSectionsShut,
  holdPinned,
  holdSectionsShut,
  chatRows,
  mergeChats,
  type ChatRow,
  type ChatsPayload,
  type ConversationsPayload,
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
  pinned: string[] | null;
  sectionsShut: string[];
};

/** Built on first read rather than at import, so the reads happen once the page is standing. */
function fresh(): RailState {
  return {
    phase: "loading",
    rows: [],
    sought: {},
    linked: {},
    fault: null,
    pinned: heldPinned(),
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

/** The listing pages, so the read follows the continuation until this many rows stand — comfortably
 *  past the conversations a member works from, so a workspace with thousands costs a bounded number of reads. */
const RAIL_ROWS_MAX = 300;

export function readRail(): void {
  const read = ++reads;
  update((held) => ({ ...held, phase: "loading" }));
  void walkRail(read);
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
      rows: mergeChats(gathered, held.rows),
    }));
    cursor = result.payload.next_cursor ?? "";
    if (!cursor || gathered.length >= RAIL_ROWS_MAX) return;
  }
}

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

/** A row's moment is the turn it started, never the turn it finished: a send that failed leaves the
 *  rail where it stood. */
export function railActivity(conversationId: string, turn: ChatTurn): void {
  if (turn !== "running") return;
  update((held) => ({ ...held, rows: bumpChat(held.rows, conversationId, new Date()) }));
}

export function quietRail(): void {
  update((held) => ({ ...held, fault: null }));
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

export function resetRailStore(): void {
  state = null;
  reads = 0;
  seeking.clear();
  visibilityChanges.clear();
}
