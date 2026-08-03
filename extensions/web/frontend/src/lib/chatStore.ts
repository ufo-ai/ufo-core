import { useSyncExternalStore } from "react";

import type { ChatFile, ChatQuestion, CredentialRequest, Message } from "@/lib/types";

export type ToolEvent = {
  kind: "tool" | "skill";
  name: string;
  preview: string;
  description: string;
};

export type Bubble = Message & {
  meta?: string;
  files?: ChatFile[];
  connectUrl?: string;
  events?: ToolEvent[];
};

export type LiveTurn = {
  text: string;
  activity: string | null;
  meter: string | null;
  meta: string | null;
  files: ChatFile[] | null;
  connectUrl: string | null;
  events: ToolEvent[];
};

export type Handoffs = {
  question?: ChatQuestion | null;
  credentials?: CredentialRequest | null;
  files?: ChatFile[] | null;
};

export type ChatState = {
  messages: Bubble[] | null;
  busy: boolean;
  live: LiveTurn | null;
  handoffs: Handoffs;
};

const EMPTY: ChatState = { messages: null, busy: false, live: null, handoffs: {} };

const states = new Map<string, ChatState>();
const listeners = new Set<() => void>();

export function chatState(agentId: string): ChatState {
  return states.get(agentId) ?? EMPTY;
}

export function updateChat(agentId: string, change: (state: ChatState) => ChatState): void {
  states.set(agentId, change(chatState(agentId)));
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
  };
}

export function useChat(agentId: string): ChatState {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => chatState(agentId),
  );
}

export function resetChatStore(): void {
  states.clear();
}
