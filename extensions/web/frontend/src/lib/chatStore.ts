import { useSyncExternalStore } from "react";

import type { ToastState } from "@/components/ui/toast";
import type {
  ActivityEvent,
  ChatApp,
  ChatConnect,
  ChatFile,
  ChatQuestion,
  CredentialRequest,
  Message,
  SubagentRun,
} from "@/lib/types";

export type { ActivityEvent } from "@/lib/types";

export type Bubble = Message & {
  meta?: string;
  /** The model the turn ran on, held as the id: the meta line draws the name and the mark the
   *  picker draws for that id. An agent on the auto sentinel carries none. */
  model?: string;
  /** How long the turn that spoke this ran, in milliseconds. A bubble read back from a transcript
   *  carries none, so its fold states the steps alone. */
  elapsed?: number;
  connect?: ChatConnect;
  sending?: string;
  attached?: File[];
  queued?: boolean;
};

export type LiveTurn = {
  started: number;
  text: string;
  activity: string | null;
  meter: string | null;
  meta: string | null;
  model: string | null;
  connect: ChatConnect | null;
  events: ActivityEvent[];
  subagents: SubagentRun[];
  files: ChatFile[];
  apps: ChatApp[];
  reconnecting: boolean;
};

export type Handoffs = {
  question?: ChatQuestion | null;
  credentials?: CredentialRequest | null;
};

export type StreamingTurn = { id: string; answering: boolean };

export type Founding = { conversationId: string; title: string };

export type ChatState = {
  messages: Bubble[] | null;
  earlierCursor: string | null;
  busy: boolean;
  live: LiveTurn | null;
  turn: StreamingTurn | null;
  /** A drain can be published before the send that admitted the row has its response back, so the ids are
   *  kept rather than only cleared off the bubbles they name. */
  absorbed: string[];
  spoken: string[];
  handoffs: Handoffs;
  fault: ToastState | null;
  founded: Founding | null;
  closed: boolean;
};

const EMPTY: ChatState = {
  messages: null,
  earlierCursor: null,
  busy: false,
  live: null,
  turn: null,
  absorbed: [],
  spoken: [],
  handoffs: {},
  fault: null,
  founded: null,
  closed: false,
};

const states = new Map<string, ChatState>();
const listeners = new Set<() => void>();

export function chatState(chatKey: string): ChatState {
  return states.get(chatKey) ?? EMPTY;
}

export function updateChat(chatKey: string, change: (state: ChatState) => ChatState): void {
  states.set(chatKey, change(chatState(chatKey)));
  for (const listener of listeners) listener();
}

/** A redraw of a turn already running hands back the start it opened with: the clock counts the turn,
 *  not the source drawing it. */
export function liveTurn(started: number = Date.now()): LiveTurn {
  return {
    started,
    text: "",
    activity: null,
    meter: null,
    meta: null,
    model: null,
    connect: null,
    events: [],
    subagents: [],
    files: [],
    apps: [],
    reconnecting: false,
  };
}

export function migrateChat(fromKey: string, toKey: string, title: string): void {
  const state = states.get(fromKey);
  if (!state) return;
  states.set(toKey, { ...state, closed: false });
  states.set(
    fromKey,
    state.closed ? EMPTY : { ...EMPTY, founded: { conversationId: toKey, title } },
  );
  for (const listener of listeners) listener();
}

export function clearChat(chatKey: string): void {
  if (!states.delete(chatKey)) return;
  for (const listener of listeners) listener();
}

export function useChat(chatKey: string): ChatState {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => chatState(chatKey),
  );
}

export function resetChatStore(): void {
  states.clear();
}
