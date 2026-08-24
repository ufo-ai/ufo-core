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
  connect?: ChatConnect;
  /** A send whose POST has not answered yet, carrying the send's own token — how the response
   *  settles its own bubble however the log has shifted around it, since an index recorded at send
   *  time stops naming the bubble the moment a drain inserts a reply ahead of it. */
  sending?: string;
  /** The files this page attached to a message it sent, held as the files themselves: the bubble
   *  draws its own pictures off them, because the conversation's workspace — which every later read
   *  draws them from — holds them only once the send has landed. */
  attached?: File[];
  /** Sent into a turn already running, so it waits for that turn to take it up: it is drawn under
   *  the stream, and while its POST is in flight it is the fold's target for a drain that names a
   *  row no bubble carries an `arrival_id` for yet. A send that opens a turn carries neither — it
   *  is the prompt the reply answers, and it stands above that reply. */
  queued?: boolean;
};

export type LiveTurn = {
  text: string;
  activity: string | null;
  meter: string | null;
  meta: string | null;
  connect: ChatConnect | null;
  events: ActivityEvent[];
  subagents: SubagentRun[];
  /** What the turn has shared so far. It settles on the reply that closes the turn, where the
   *  durable transcript states it. */
  files: ChatFile[];
  /** The applications the turn created, settling on the same reply for the same reason. */
  apps: ChatApp[];
  reconnecting: boolean;
};

export type Handoffs = {
  question?: ChatQuestion | null;
  credentials?: CredentialRequest | null;
};

export type StreamingTurn = { id: string; answering: boolean };

/** The conversation a founding send on this key opened. `migrateChat` moves the state to the
 *  conversation's own key and leaves this behind as a forwarding record, so a pane that lost the
 *  founding callback — unmounted mid-flight, or never the sender at all — still learns where its
 *  conversation went by reading the key it was watching. */
export type Founding = { conversationId: string; title: string };

export type ChatState = {
  messages: Bubble[] | null;
  /** The compacted-away page above `messages`, as the transcript read stated it. */
  earlierCursor: string | null;
  busy: boolean;
  live: LiveTurn | null;
  turn: StreamingTurn | null;
  /** The arrival ids a turn has said it took up. A drain can be published before the send that
   *  admitted the row has its response back, so the ids are kept rather than only cleared off the
   *  bubbles they name: a message whose id is already here never states a wait it will not leave. */
  absorbed: string[];
  /** The ids of the replies the turn has already delivered mid-flight, so a frame replayed after a
   *  reconnect — or republished by a recovered turn — states its reply once. */
  spoken: string[];
  handoffs: Handoffs;
  fault: ToastState | null;
  founded: Founding | null;
  /** The pane this key feeds was closed while its founding send was in flight. The landing still
   *  migrates the live state to the conversation's own key, but leaves no forwarding record, so
   *  nothing raises the closed run again; a pane that reopens first clears this and joins it. */
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

export function liveTurn(): LiveTurn {
  return {
    text: "",
    activity: null,
    meter: null,
    meta: null,
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
