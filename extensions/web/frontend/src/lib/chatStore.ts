import { useSyncExternalStore } from "react";

import type { ToastState } from "@/components/ui/toast";
import type {
  ActivityEvent,
  ChatFile,
  ChatQuestion,
  CredentialRequest,
  Message,
  SubagentRun,
} from "@/lib/types";

export type { ActivityEvent } from "@/lib/types";

export type Bubble = Message & {
  meta?: string;
  files?: ChatFile[];
  connectUrl?: string;
  /** A send whose POST has not answered yet, carrying the send's own token. The queue row is
   *  committed before the response returns, so a drain can name it while no bubble holds the
   *  `arrival_id` — the marker is the fold's target in that window, and the drain that folds the
   *  row consumes it. The token is how the response settles its own bubble however the log has
   *  shifted around it: an index recorded at send time stops naming the bubble the moment a drain
   *  inserts a reply ahead of it. */
  sending?: string;
};

export type LiveTurn = {
  text: string;
  activity: string | null;
  meter: string | null;
  meta: string | null;
  files: ChatFile[] | null;
  connectUrl: string | null;
  events: ActivityEvent[];
  subagents: SubagentRun[];
  reconnecting: boolean;
};

export type Handoffs = {
  question?: ChatQuestion | null;
  credentials?: CredentialRequest | null;
  files?: ChatFile[] | null;
};

export type StreamingTurn = { id: string; answering: boolean };

export type ChatState = {
  messages: Bubble[] | null;
  busy: boolean;
  live: LiveTurn | null;
  turn: StreamingTurn | null;
  /** The arrival ids a turn has said it took up. A drain can be published before the send that
   *  admitted the row has its response back, so the ids are kept rather than only cleared off the
   *  bubbles they name: a message whose id is already here never states a wait it will not leave. */
  absorbed: string[];
  handoffs: Handoffs;
  fault: ToastState | null;
};

const EMPTY: ChatState = {
  messages: null,
  busy: false,
  live: null,
  turn: null,
  absorbed: [],
  handoffs: {},
  fault: null,
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

export function liveTurn(): LiveTurn {
  return {
    text: "",
    activity: null,
    meter: null,
    meta: null,
    files: null,
    connectUrl: null,
    events: [],
    subagents: [],
    reconnecting: false,
  };
}

export function migrateChat(fromKey: string, toKey: string): void {
  const state = states.get(fromKey);
  if (!state) return;
  states.delete(fromKey);
  states.set(toKey, state);
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
