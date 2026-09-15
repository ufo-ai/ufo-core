import { useSyncExternalStore } from "react";

import type { ToastState } from "@/components/ui/toast";
import type { Bubble, LiveTurn } from "@/lib/turnRecord";
import type { CredentialRequest } from "@/lib/types";

/** What a conversation's turn is doing, as a screen outside the chat reads it. */
export type ChatTurn = "running" | "idle" | "parked";

export type Founding = { conversationId: string; title: string };

export type ChatState = {
  messages: Bubble[] | null;
  earlierCursor: string | null;
  busy: boolean;
  /** The turn this page is watching: the record its stream builds, or the placeholder a send holds
   *  until the surface names the turn it opened. */
  live: LiveTurn | null;
  credentials: CredentialRequest | null;
  fault: ToastState | null;
  founded: Founding | null;
  closed: boolean;
  /** Where the turn landed when its stream ended, or null while one runs and after a stream that
   *  dropped without a verdict — the server's turn outlives that drop, so no screen restates it. */
  ended: ChatTurn | null;
};

const EMPTY: ChatState = {
  messages: null,
  earlierCursor: null,
  busy: false,
  live: null,
  credentials: null,
  fault: null,
  founded: null,
  closed: false,
  ended: null,
};

const states = new Map<string, ChatState>();
const listeners = new Set<() => void>();

export function chatState(chatKey: string): ChatState {
  return states.get(chatKey) ?? EMPTY;
}

/** The turn whose stream this chat tails, or null while none is named: a send in flight holds a
 *  record with no id yet, and that is not a turn a second send can join. */
export function attachedTurn(state: ChatState): string | null {
  return state.live?.id ?? null;
}

export function updateChat(chatKey: string, change: (state: ChatState) => ChatState): void {
  states.set(chatKey, change(chatState(chatKey)));
  for (const listener of listeners) listener();
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
