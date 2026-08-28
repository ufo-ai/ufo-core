import { useSyncExternalStore } from "react";

import { getJson } from "@/lib/api";

/** What every app in the workspace is doing, read once for the whole page. The sidebar draws the
 *  rows wherever the member is, so the answer cannot belong to one screen's mount — it is held
 *  here and every reader takes the same one, the way `railStore` holds the rail.
 *
 *  The read runs only while something is drawing it: the listener set is the signal, so the last
 *  reader to leave stops the poll and the first to arrive asks at once. */

/** One app's live picture: the liveest turn it holds, what that turn is doing when the engine
 *  said so, and the marks that place a resting app in time. */
export type AgentStatus = {
  agent_id: string;
  turn: "running" | "queued" | "parked" | null;
  activity: string | null;
  last_active_at: string | null;
  last_failed: boolean;
};

export type AppStatusState = {
  statuses: Readonly<Record<string, AgentStatus>>;
  /** Whether any app holds work in flight, which is what sets the rate the read runs at. */
  working: boolean;
};

/** How often the store asks what the apps are doing: at the rate a step changes while any of them
 *  is working, and at the panel's own resting rate when none is. A step an app takes is over in
 *  seconds, so a line naming one is only true if it is re-read at that rate — but a workspace of
 *  resting apps says the same thing every time it is asked. */
export const WORKING_STATUS_MS = 4_000;
export const RESTING_STATUS_MS = 30_000;

const STATUS_PATH = "/api/agents/status";

const QUIET: AppStatusState = { statuses: {}, working: false };

let state: AppStatusState = QUIET;
const listeners = new Set<() => void>();
let timer: number | null = null;
let reading = false;

/** Which run of the store a read belongs to. Torn down and started again — a page moving on, a test
 *  clearing between cases — the read already in flight cannot be recalled, so it is stamped and the
 *  answer to an older stamp is dropped. Without it a read begun under one subscription lands its
 *  statuses on the next. */
let generation = 0;

/** Whether any read has ever answered. One that never has waits for the member's next move once it
 *  fails: there is nothing on the screen for a retry to correct. */
let answered = false;

/** Whether a turn was started in this browser since the last answer. The read that follows a send
 *  can land before the engine holds the turn, so one further read is taken at the working rate
 *  before the store settles back to resting. Without it a member watching their own turn run would
 *  read a sidebar saying the workspace was quiet until the resting tick came round. */
let stirred = false;

/** The turns this browser itself admitted, each held by the chat that carries it, mapped to the
 *  app that runs it. Work the member watched begin is marked at once rather than when the next
 *  poll lands: a short turn can start and finish wholly between ticks, and the sidebar would say
 *  the workspace was quiet while the member read the reply arriving. The wire owns every other
 *  agent's state; a held app the wire already shows working is the wire's row untouched. A hold
 *  ends where the turn does — the stream's close, or the failure that ends the send — never on a
 *  wire answer, because the answer may have been asked before the engine held the turn. */
const heldTurns = new Map<string, string>();

let wired: Readonly<Record<string, AgentStatus>> = {};

function restate(): void {
  const statuses: Record<string, AgentStatus> = { ...wired };
  for (const agentId of heldTurns.values()) {
    const known = statuses[agentId];
    if (known && (known.turn === "running" || known.turn === "queued")) continue;
    statuses[agentId] = {
      agent_id: agentId,
      turn: "queued",
      activity: null,
      last_active_at: known?.last_active_at ?? null,
      last_failed: known?.last_failed ?? false,
    };
  }
  state = {
    statuses,
    working:
      heldTurns.size > 0 ||
      Object.values(wired).some(
        (status) => status.turn === "running" || status.turn === "queued",
      ),
  };
  for (const listener of listeners) listener();
}

export function holdTurn(chatKey: string, agentId: string): void {
  heldTurns.set(chatKey, agentId);
  restate();
  wakeAppStatus();
}

export function releaseTurn(chatKey: string): void {
  if (!heldTurns.delete(chatKey)) return;
  restate();
}

/** A founding chat settles onto the conversation the send opened, and the hold moves with it so
 *  the close that ends the turn finds it under the key the stream carries. */
export function moveTurnHold(from: string, to: string): void {
  const agentId = heldTurns.get(from);
  if (agentId === undefined) return;
  heldTurns.delete(from);
  heldTurns.set(to, agentId);
}

function appStatusState(): AppStatusState {
  return state;
}

export function useAppStatus(): AppStatusState {
  return useSyncExternalStore(subscribe, appStatusState);
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (listeners.size === 1) {
    document.addEventListener("visibilitychange", woken);
    ask();
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) quiet();
  };
}

/** A tab that is looked at again reads at once rather than serving what it held until the next
 *  tick, and one that is put away stops asking until it is back. */
function woken(): void {
  if (document.visibilityState === "hidden") {
    if (timer !== null) window.clearTimeout(timer);
    timer = null;
    return;
  }
  ask();
}

function ask(): void {
  if (timer === null && !reading) void read();
}

async function read(): Promise<void> {
  timer = null;
  reading = true;
  const run = generation;
  try {
    const result = await getJson<{ statuses: AgentStatus[] }>(STATUS_PATH);
    if (run !== generation) return;
    if (result.ok) {
      answered = true;
      hold(result.payload.statuses);
    } else if (!answered) return;
    poll();
  } finally {
    /* The flag says a read is out, so it is given back whatever the read did. An answer that threw
       — a 200 whose body is not the shape it claims — would otherwise leave it standing, and every
       later ask and wake would find a read already in flight and do nothing: the dots would hold
       whatever they last said for the rest of the session. */
    if (run === generation) reading = false;
  }
}

/** A read that fails keeps the answer before it: a poll that landed as a blank would take the
 *  status dots off a drawn row until the next tick. */
let wiredSaid = "";

function hold(statuses: AgentStatus[]): void {
  /* The read crosses the wire, so what it carries is checked before it is believed: an answer
     without the rows it claims is no answer, and the last good one stands. A workspace at rest
     answers the same thing every tick, and a listener told about it would redraw the rail and
     every framed lane for nothing — an answer that changed nothing changes nothing. */
  if (!Array.isArray(statuses)) return;
  const said = JSON.stringify(statuses);
  if (said === wiredSaid) return;
  wiredSaid = said;
  wired = Object.fromEntries(statuses.map((status) => [status.agent_id, status]));
  restate();
}

function poll(): void {
  if (listeners.size === 0 || document.visibilityState === "hidden") return;
  const soon = state.working || stirred;
  stirred = false;
  timer = window.setTimeout(read, soon ? WORKING_STATUS_MS : RESTING_STATUS_MS);
}

/** Ask now, because something just happened here that the next tick is too far away to state: a
 *  turn sent from this browser is work the member can already see beginning, and a sidebar is the
 *  one place they look to know what the workspace is doing. */
export function wakeAppStatus(): void {
  if (listeners.size === 0) return;
  stirred = true;
  if (timer !== null) {
    clearTimeout(timer);
    timer = null;
  }
  ask();
}

/** Stop asking. The page is not always there to let go of — a suite that never rendered one still
 *  clears this store between its tests — so the listener is dropped only where a document stands. */
function quiet(): void {
  generation += 1;
  reading = false;
  heldTurns.clear();
  wired = {};
  wiredSaid = "";
  state = QUIET;
  if (typeof document !== "undefined") {
    document.removeEventListener("visibilitychange", woken);
  }
  if (timer !== null) clearTimeout(timer);
  timer = null;
}

export function resetAppStatusStore(): void {
  quiet();
  generation += 1;
  listeners.clear();
  state = QUIET;
  reading = false;
  answered = false;
  stirred = false;
}

/** The dot the mark wears, read before any words: the live tone while the app holds work in
 *  flight, and the blocked tone while it is waiting on the member.
 *
 *  An app that is paused, whose last run failed, or that was installed and never set up are one
 *  state to a member scanning a column — none of them is going to do anything until they act. They
 *  differ in what to do next, which is the row's own screen to say, not a second colour's.
 *
 *  Work outranks the rest: an app that is running is telling the member something is happening now,
 *  and that is true whether or not its setup is finished.
 *
 *  The waiting tone breathes: an app that wants the member keeps asking until they act, so its dot
 *  carries the working pulse for as long as it stands — and stands still for a member who asked
 *  motion to. */
export function statusDot(status: AgentStatus | undefined, setupDue: boolean): string | null {
  if (status?.turn === "running" || status?.turn === "queued") return "bg-live";
  if (setupDue || status?.turn === "parked" || status?.last_failed) {
    return "bg-blocked animate-working motion-reduce:animate-none";
  }
  return null;
}
