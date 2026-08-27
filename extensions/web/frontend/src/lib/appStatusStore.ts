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
function hold(statuses: AgentStatus[]): void {
  /* The read crosses the wire, so what it carries is checked before it is believed: an answer
     without the rows it claims is no answer, and the last good one stands. */
  if (!Array.isArray(statuses)) return;
  state = {
    statuses: Object.fromEntries(statuses.map((status) => [status.agent_id, status])),
    working: statuses.some((status) => status.turn === "running" || status.turn === "queued"),
  };
  for (const listener of listeners) listener();
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
