import { useSyncExternalStore } from "react";

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
  handoffs: Handoffs;
};

const EMPTY: ChatState = { messages: null, busy: false, live: null, turn: null, handoffs: {} };

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
