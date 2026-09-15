import { useSyncExternalStore } from "react";

import { getJson } from "@/lib/api";

export type AgentStatus = {
  agent_id: string;
  turn: "running" | "queued" | "parked" | null;
  activity: string | null;
  last_active_at: string | null;
  last_failed: boolean;
};

export type AppStatusState = {
  statuses: Readonly<Record<string, AgentStatus>>;
  working: boolean;
  outOfCredit: boolean;
};

/** A step an app takes is over in seconds, so a line naming one is only true if it is re-read at that
 *  rate — but a workspace of resting apps says the same thing every time it is asked. */
export const WORKING_STATUS_MS = 4_000;
export const RESTING_STATUS_MS = 30_000;

const STATUS_PATH = "/api/agents/status";

const QUIET: AppStatusState = { statuses: {}, working: false, outOfCredit: false };

let state: AppStatusState = QUIET;
const listeners = new Set<() => void>();
let timer: number | null = null;
let reading = false;

let generation = 0;

let answered = false;

/** The read that follows a send can land before the engine holds the turn, so one further read is taken
 *  at the working rate before the store settles back to resting. */
let stirred = false;

/** A hold ends where the turn does — the stream's close, or the failure that ends the send — never on a
 *  wire answer, because the answer may have been asked before the engine held the turn. */
const heldTurns = new Map<string, string>();

let wired: Readonly<Record<string, AgentStatus>> = {};
let wiredOutOfCredit = false;

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
    outOfCredit: wiredOutOfCredit,
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
    const result = await getJson<{ statuses: AgentStatus[]; out_of_credit: boolean }>(
      STATUS_PATH,
    );
    if (run !== generation) return;
    if (result.ok) {
      answered = true;
      hold(result.payload.statuses, result.payload.out_of_credit);
    } else if (!answered) return;
    poll();
  } finally {
    /* The flag is given back whatever the read did: an answer that threw would leave it standing, and
       every later ask would find a read already in flight and do nothing. */
    if (run === generation) reading = false;
  }
}

let wiredSaid = "";

function hold(statuses: AgentStatus[], outOfCredit: boolean): void {
  const said = JSON.stringify([statuses, outOfCredit]);
  if (said === wiredSaid) return;
  wiredSaid = said;
  /* The two halves of the answer stand or fall on their own: a malformed status list must not take
     the credit line down with it, since that line is what explains why the agents stopped. */
  if (Array.isArray(statuses)) {
    wired = Object.fromEntries(statuses.map((status) => [status.agent_id, status]));
  }
  wiredOutOfCredit = outOfCredit === true;
  restate();
}

function poll(): void {
  if (listeners.size === 0 || document.visibilityState === "hidden") return;
  const soon = state.working || stirred;
  stirred = false;
  timer = window.setTimeout(read, soon ? WORKING_STATUS_MS : RESTING_STATUS_MS);
}

export function wakeAppStatus(): void {
  if (listeners.size === 0) return;
  stirred = true;
  if (timer !== null) {
    clearTimeout(timer);
    timer = null;
  }
  ask();
}

function quiet(): void {
  generation += 1;
  reading = false;
  heldTurns.clear();
  wired = {};
  wiredOutOfCredit = false;
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

export function statusDot(status: AgentStatus | undefined, setupDue: boolean): string | null {
  if (status?.turn === "running" || status?.turn === "queued") return "bg-live";
  if (setupDue || status?.turn === "parked" || status?.last_failed) {
    return "bg-blocked animate-working motion-reduce:animate-none";
  }
  return null;
}
