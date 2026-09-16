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
  filedChat,
  mergeChats,
  railRows,
  readChat,
  resolvedConversation,
  type ConversationDetailPayload,
  type ConversationsPayload,
  type RailShown,
  type RailSort,
} from "@/lib/rail";
import type { Conversation } from "@/lib/types";

export type RailPhase = "loading" | "failed" | "ready";

/** What the resolve answered instead of the conversation. A refusal outranks a record the tab
 *  holds: the resolve is the one read that answers for the open conversation, and the rail may
 *  still list what it refused. A read that failed answers for the read alone, so the held record
 *  outranks it. */
export type Sought =
  | { kind: "absent" }
  | { kind: "signed-out" }
  | { kind: "failed"; message: string };

export type RailState = {
  phase: RailPhase;
  /** The listing, newest activity first. */
  rows: Conversation[];
  sought: Readonly<Record<string, Sought>>;
  /** Every conversation this tab holds a record of, by id: the listed rows, the ones a permalink
   *  resolved past the listing's bound, and the one a send just founded. */
  known: Readonly<Record<string, Conversation>>;
  /** What the reads behind the rail have answered, counted, so a screen laying its member's own
   *  act over these rows knows a read has answered since the act. */
  answers: number;
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
    known: {},
    answers: 0,
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
  const known = held.known[conversationId];
  return withRows(
    known ? { ...held, known: { ...held.known, [conversationId]: { ...known, ...next } } } : held,
    held.rows.map((entry) =>
      entry.conversation_id === conversationId ? { ...entry, ...next } : entry,
    ),
  );
}

/** The listing's rows are known rows too, so every write to the listing lands in both. */
function withRows(held: RailState, rows: Conversation[]): RailState {
  const known = { ...held.known };
  for (const row of rows) known[row.conversation_id] = row;
  return { ...held, rows, known };
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

/** A retry answering after the read it replaced would otherwise put the older rows back, and so
 *  would a read issued before a filing act: both are voided by raising this generation. */
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
  const gathered: Conversation[] = [];
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
    update((held) =>
      withRows(
        { ...held, phase: "ready" as const, cut: result.payload.cut === true, answers: held.answers + 1 },
        mergeChats(gathered, held.rows),
      ),
    );
    cursor = result.payload.next_cursor ?? "";
    if (!cursor || gathered.length >= RAIL_ROWS_MAX) return;
  }
}

/** One page answers the whole rail's dots: a running turn's own updates put its conversation at
 *  the head of the `last_at` order. It is also the one read a member's own act runs behind its
 *  optimistic write, rather than the whole `walkRail` continuation, and a filing voids the walk the
 *  rail may be waiting on, so this answer is what ends that wait. */
export async function freshenRail(): Promise<void> {
  const read = reads;
  const params = new URLSearchParams({ order_by: "last_at", order: "desc" });
  const result = await getJson<ConversationsPayload>("/objects/conversation?" + params.toString());
  if (read !== reads || !result.ok) return;
  const fetched = chatRows(result.payload).map((row) => applyVisibility(row.conversation_id, row));
  update((held) =>
    withRows({ ...held, phase: "ready" as const, answers: held.answers + 1 }, mergeChats(fetched, held.rows)),
  );
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
      void freshenRail().then(poll);
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

/** The conversations this tab founded and has not left yet: each holds the row its own send wrote,
 *  so its first visit reads nothing back. */
const founded = new Set<string>();

/** Every visit reads the conversation again, so its title line follows a rename or a change of
 *  audience made since the last one; the record held meanwhile stands until the answer lands. */
export function seekChat(conversationId: string): void {
  if (seeking.has(conversationId) || founded.delete(conversationId)) return;
  seeking.add(conversationId);
  void getJson<ConversationDetailPayload>("/objects/conversation/" + conversationId).then(
    (result) => {
      seeking.delete(conversationId);
      if (result.ok) {
        const resolved = applyVisibility(conversationId, resolvedConversation(result.payload));
        update((current) => {
          const { [conversationId]: _refused, ...sought } = current.sought;
          return { ...current, sought, known: { ...current.known, [conversationId]: resolved } };
        });
        return;
      }
      const outcome: Sought =
        result.status === 404
          ? { kind: "absent" }
          : result.status === 401
            ? { kind: "signed-out" }
            : { kind: "failed", message: result.message };
      update((current) => ({
        ...current,
        sought: { ...current.sought, [conversationId]: outcome },
      }));
    },
  );
}

/** The founding client holds every fact the row carries, so the conversation is listed and opens
 *  without a read of what it just wrote. */
export function railFounded(row: Conversation): void {
  founded.add(row.conversation_id);
  update((held) => withRows(held, mergeChats(held.rows, [row])));
  wakeRail();
}

export function changeRailVisibility(
  conversationId: string,
  audience: string,
  memberEmail: string | null,
): boolean {
  const held = railState();
  const current = held.known[conversationId];
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

/** The change stands or is put back, and the read behind it answers for the conversation. No read
 *  issued before this one answers for the audience after it — the change is laid over what lands
 *  only while it is held here — so raising the read generation voids them. */
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
  reads += 1;
  void freshenRail();
}

/** A conversation this member just filed: the act stands in the rail ahead of the read that will
 *  state it. A row the act took off the listing leaves — the reads behind the rail stop naming it,
 *  and a merge holds every row a read does not name — and any other filing leaves the row under the
 *  marks the act set, listed again where the act brought it back.
 *
 *  No read issued before the act answers for the state after it: a merge holds every row a read
 *  does not name, so a listing read in flight — the walk behind the first draw, the status poll
 *  behind a running turn — would put the filed row back. Raising the read generation voids them,
 *  and the act's own `freshenRail` behind this call is the first read that stands. */
export function railFiled(row: Conversation): void {
  update((held) =>
    row.archived || row.deleted
      ? { ...held, rows: filedChat(held.rows, row.conversation_id) }
      : withRows(held, mergeChats([row], held.rows)),
  );
  reads += 1;
}

export function railRead(conversationId: string): void {
  update((held) => withRows(held, readChat(held.rows, conversationId)));
}

export function railActivity(conversationId: string, turn: ChatTurn): void {
  update((held) =>
    withRows(
      held,
      turn === "running"
        ? bumpChat(held.rows, conversationId, new Date(), turn)
        : turnedChat(held.rows, conversationId, turn),
    ),
  );
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
  founded.clear();
  visibilityChanges.clear();
  if (ticking !== null) window.clearTimeout(ticking);
  ticking = null;
  document.removeEventListener("visibilitychange", woken);
}
