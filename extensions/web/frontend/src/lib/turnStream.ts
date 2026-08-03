import { BASE, getJson } from "@/lib/api";
import { money } from "@/lib/money";
import {
  chatState,
  liveTurn,
  migrateChat,
  updateChat,
  type LiveTurn,
  type ToolEvent,
} from "@/lib/chatStore";
import type { ChatFile, ChatQuestion, Transcript } from "@/lib/types";

const REATTACH_DELAYS_MS = [1_000, 2_000, 4_000, 8_000, 16_000, 30_000];
const MALFORMED_REPLY = "Malformed reply — try again.";

export const NEW_CONVERSATION = "new";

/** One conversation as the client addresses it: the store key it holds, the agent and
 *  conversation the routes name, and what a create hands back to the shell. */
export type ChatTarget = {
  key: string;
  agentId: string;
  conversationId: string | null;
  onCreated?: (conversationId: string, title: string) => void;
  onAccepted?: (conversationId: string) => void;
};

function chatUrl(target: ChatTarget): string {
  const base = BASE + "/agents/" + target.agentId + "/chat";
  return base + "?conversation=" + (target.conversationId ?? NEW_CONVERSATION);
}

function transcriptPath(target: ChatTarget): string | null {
  if (!target.conversationId) return null;
  return "/agents/" + target.agentId + "/transcript?conversation=" + target.conversationId;
}

export function tokens(count: number): string {
  return count.toLocaleString("en-US");
}

const RESYNC_EPOCH = new Map<string, number>();

function bumpEpoch(chatKey: string): void {
  RESYNC_EPOCH.set(chatKey, (RESYNC_EPOCH.get(chatKey) ?? 0) + 1);
}

const SOURCES = new Map<string, EventSource>();
const REATTACHES = new Map<string, number>();
const TIMERS = new Map<string, ReturnType<typeof setTimeout>>();

type Timer = (fn: () => void, ms: number) => ReturnType<typeof setTimeout>;
const NATIVE_TIMER: Timer = (fn, ms) => setTimeout(fn, ms);
let reattachTimer: Timer = NATIVE_TIMER;

export function setReattachTimer(timer: Timer): void {
  reattachTimer = timer;
}

export function resetStreams(): void {
  for (const source of SOURCES.values()) source.close();
  for (const timer of TIMERS.values()) clearTimeout(timer);
  SOURCES.clear();
  REATTACHES.clear();
  TIMERS.clear();
  reattachTimer = NATIVE_TIMER;
}

export function eventLabel(event: ToolEvent, phase: "active" | "done"): string {
  if (event.description) return event.description;
  if (event.kind === "skill") {
    return (phase === "active" ? "Loading skill" : "Loaded skill") + " · " + event.name;
  }
  return event.preview ? event.name + " " + event.preview : event.name;
}

export function streamTurn(chatKey: string, turnId: string, answering: boolean): void {
  REATTACHES.delete(chatKey);
  updateChat(chatKey, (state) => ({ ...state, turn: { id: turnId, answering } }));
  attach(chatKey, turnId, answering, false);
}

function attach(chatKey: string, turnId: string, answering: boolean, reattach: boolean): void {
  TIMERS.delete(chatKey);
  let redrawOnOpen = reattach;
  const source = new EventSource(BASE + "/turns/" + turnId + "/stream");
  SOURCES.set(chatKey, source);
  let sawFiles = false;

  const onLive = (change: (live: LiveTurn) => LiveTurn) =>
    updateChat(chatKey, (state) => ({ ...state, live: change(state.live ?? liveTurn()) }));

  const record = () =>
    updateChat(chatKey, (state) => {
      const live = state.live;
      if (!live || !live.text) return state;
      return {
        ...state,
        messages: (state.messages ?? []).concat({
          role: "assistant",
          text: live.text,
          ...(live.meta ? { meta: live.meta } : {}),
          ...(live.files ? { files: live.files } : {}),
          ...(live.connectUrl ? { connectUrl: live.connectUrl } : {}),
          ...(live.events.length ? { events: live.events } : {}),
        }),
      };
    });

  const close = () => {
    source.close();
    SOURCES.delete(chatKey);
    updateChat(chatKey, (state) => ({ ...state, busy: false, live: null, turn: null }));
  };

  source.addEventListener("open", () => {
    REATTACHES.delete(chatKey);
    if (redrawOnOpen) {
      redrawOnOpen = false;
      updateChat(chatKey, (state) => ({ ...state, live: liveTurn() }));
    } else {
      onLive((live) => ({ ...live, reconnecting: false }));
    }
  });

  source.onmessage = (event) => {
    const chunk = JSON.parse(event.data).text as string;
    onLive((live) => ({ ...live, text: live.text + chunk }));
  };

  source.addEventListener("files", (event) => {
    const files = JSON.parse((event as MessageEvent).data).files as ChatFile[];
    sawFiles = true;
    updateChat(chatKey, (state) => ({
      ...state,
      handoffs: { ...state.handoffs, files },
      live: { ...(state.live ?? liveTurn()), files },
    }));
  });

  source.addEventListener("credentials", (event) => {
    const credentials = JSON.parse((event as MessageEvent).data);
    updateChat(chatKey, (state) => ({
      ...state,
      handoffs: { ...state.handoffs, credentials },
    }));
  });

  source.addEventListener("tool", (event) => {
    const frame = JSON.parse((event as MessageEvent).data);
    const entry: ToolEvent = {
      kind: "tool",
      name: frame.tool,
      preview: frame.preview ?? "",
      description: frame.description ?? "",
    };
    onLive((live) => ({
      ...live,
      events: live.events.concat(entry),
      activity: eventLabel(entry, "active"),
    }));
  });

  source.addEventListener("skill", (event) => {
    const frame = JSON.parse((event as MessageEvent).data);
    const entry: ToolEvent = { kind: "skill", name: frame.skill, preview: "", description: "" };
    onLive((live) => ({
      ...live,
      events: live.events.concat(entry),
      activity: eventLabel(entry, "active"),
    }));
  });

  source.addEventListener("cost", (event) => {
    const frame = JSON.parse((event as MessageEvent).data);
    onLive((live) => ({
      ...live,
      meter: tokens(frame.tokens) + " tok · " + money(frame.cost_micro_usd),
    }));
  });

  source.addEventListener("connect", (event) => {
    const frame = JSON.parse((event as MessageEvent).data);
    onLive((live) => ({ ...live, connectUrl: frame.url }));
  });

  source.addEventListener("connect_error", (event) => {
    const frame = JSON.parse((event as MessageEvent).data);
    onLive((live) => ({ ...live, activity: frame.message }));
  });

  source.addEventListener("terminal", (event) => {
    const frame = JSON.parse((event as MessageEvent).data);
    updateChat(chatKey, (state) => {
      const live = state.live ?? liveTurn();
      const handoffs = { ...state.handoffs };
      let text = live.text;
      let meta = live.meta;
      if (frame.status === "done") {
        if (frame.text) text = frame.text;
        meta = frame.model + " · " + tokens(frame.tokens) + " tok · " + money(frame.cost_micro_usd);
        if (frame.question) {
          handoffs.question = { turn_id: turnId, ...frame.question };
        } else if (!answering) {
          handoffs.question = null;
        }
      } else {
        const fallback =
          frame.text ||
          "(" + frame.status + (frame.error_class ? ": " + frame.error_class : "") + ")";
        text = text ? text + "\n" + fallback : fallback;
        if (!answering) handoffs.question = null;
      }
      if (!sawFiles) handoffs.files = null;
      return { ...state, handoffs, live: { ...live, text, meta } };
    });
    record();
    close();
  });

  source.addEventListener("parked", (event) => {
    const message = JSON.parse((event as MessageEvent).data).message as string;
    onLive((live) => ({ ...live, text: live.text ? live.text + "\n" + message : message }));
    record();
    close();
  });

  source.onerror = () => {
    if (source.readyState !== EventSource.CLOSED) {
      onLive((live) => ({ ...live, reconnecting: true }));
      return;
    }
    source.close();
    SOURCES.delete(chatKey);
    const attempts = (REATTACHES.get(chatKey) ?? 0) + 1;
    if (attempts > REATTACH_DELAYS_MS.length) {
      record();
      updateChat(chatKey, (state) => ({
        ...state,
        messages: (state.messages ?? []).concat({
          role: "error",
          text: "Connection lost — reload to see the reply.",
        }),
      }));
      close();
      return;
    }
    REATTACHES.set(chatKey, attempts);
    onLive((live) => ({ ...live, reconnecting: true }));
    TIMERS.set(
      chatKey,
      reattachTimer(() => attach(chatKey, turnId, answering, true), REATTACH_DELAYS_MS[attempts - 1]),
    );
  };
}

export function resyncChat(target: ChatTarget): void {
  const chatKey = target.key;
  const state = chatState(chatKey);
  if (state.turn) {
    const source = SOURCES.get(chatKey);
    if (source && source.readyState !== EventSource.CLOSED) return;
    const timer = TIMERS.get(chatKey);
    if (timer !== undefined) clearTimeout(timer);
    attach(chatKey, state.turn.id, state.turn.answering, true);
    return;
  }
  if (state.busy || state.messages === null) return;
  void refreshTranscript(target);
}

export async function refreshTranscript(
  target: ChatTarget,
  onlyIfEmpty = false,
): Promise<void> {
  const chatKey = target.key;
  if (onlyIfEmpty && chatState(chatKey).messages !== null) return;
  const path = transcriptPath(target);
  if (path === null) {
    updateChat(chatKey, (current) =>
      current.messages !== null ? current : { ...current, messages: [] },
    );
    return;
  }
  const epoch = RESYNC_EPOCH.get(chatKey) ?? 0;
  const result = await getJson<Transcript>(path);
  if (!result.ok && !onlyIfEmpty) return;
  const payload: Transcript = result.ok ? result.payload : { messages: [] };
  updateChat(chatKey, (current) => {
    if (onlyIfEmpty && current.messages !== null) return current;
    if ((RESYNC_EPOCH.get(chatKey) ?? 0) !== epoch) return current;
    if (!onlyIfEmpty && (current.busy || current.turn)) return current;
    const incoming = ("question" in payload && payload.question) || null;
    const held = current.handoffs.question ?? null;
    const question =
      incoming === null
        ? held
        : held && held.turn_id === incoming.turn_id
          ? { ...incoming, answered: held.answered }
          : incoming;
    return {
      ...current,
      messages: payload.messages,
      handoffs: {
        question,
        credentials: ("credentials" in payload && payload.credentials) || null,
        files: ("files" in payload && payload.files) || null,
      },
    };
  });
}

export async function sendMessage(
  target: ChatTarget,
  body: string | FormData,
  shown: string,
): Promise<void> {
  const chatKey = target.key;
  bumpEpoch(chatKey);
  updateChat(chatKey, (state) => ({
    ...state,
    busy: true,
    live: liveTurn(),
    messages: (state.messages ?? []).concat({ role: "user", text: shown }),
  }));
  let res: Response;
  try {
    res = await fetch(chatUrl(target), {
      method: "POST",
      body,
      credentials: "same-origin",
    });
  } catch {
    failTurn(chatKey, "Network error — try again.");
    return;
  }
  if (!res.ok) {
    failTurn(chatKey, "Error " + res.status + " — try again.");
    return;
  }
  let accepted: { turn_id?: unknown; conversation_id?: unknown; title?: unknown };
  try {
    accepted = await res.json();
  } catch {
    failTurn(chatKey, "Network error — try again.");
    return;
  }
  if (typeof accepted.turn_id !== "string") {
    failTurn(chatKey, MALFORMED_REPLY);
    return;
  }
  let streamKey = chatKey;
  if (target.conversationId) {
    target.onAccepted?.(target.conversationId);
  } else {
    if (typeof accepted.conversation_id !== "string" || typeof accepted.title !== "string") {
      failTurn(chatKey, MALFORMED_REPLY);
      return;
    }
    streamKey = accepted.conversation_id;
    migrateChat(chatKey, streamKey);
    target.onCreated?.(accepted.conversation_id, accepted.title);
  }
  streamTurn(streamKey, accepted.turn_id, false);
}

export async function answerQuestion(
  target: ChatTarget,
  turnId: string,
  questionIndex: number,
  body: string,
): Promise<void> {
  const chatKey = target.key;
  const state = chatState(chatKey);
  if (state.busy || state.messages === null) return;
  bumpEpoch(chatKey);
  updateChat(chatKey, (current) => ({ ...current, busy: true, live: liveTurn() }));
  let res: Response;
  try {
    res = await fetch(chatUrl(target), {
      method: "POST",
      body,
      credentials: "same-origin",
      headers: {
        "x-ufo-answer-turn": turnId,
        "x-ufo-answer-question": String(questionIndex),
      },
    });
  } catch {
    failTurn(chatKey, "Network error — try again.");
    return;
  }
  if (!res.ok) {
    failTurn(chatKey, "Error " + res.status + " — try again.");
    return;
  }
  let payload: { body?: unknown; turn_id?: unknown };
  try {
    payload = await res.json();
  } catch {
    failTurn(chatKey, "Network error — try again.");
    return;
  }
  if (typeof payload.turn_id !== "string") {
    failTurn(chatKey, MALFORMED_REPLY);
    return;
  }
  const turn = payload.turn_id;
  const landed = typeof payload.body === "string" && payload.body ? payload.body : body;
  updateChat(chatKey, (current) => ({
    ...current,
    messages: (current.messages ?? []).concat({ role: "user", text: landed }),
    handoffs: {
      ...current.handoffs,
      question: markAnswered(current.handoffs.question, questionIndex, turnId),
    },
  }));
  streamTurn(chatKey, turn, true);
}

function failTurn(chatKey: string, message: string): void {
  updateChat(chatKey, (state) => ({
    ...state,
    busy: false,
    live: null,
    messages: (state.messages ?? []).concat({ role: "error", text: message }),
  }));
}

export function markAnswered(
  question: ChatQuestion | null | undefined,
  index: number,
  turnId: string,
): ChatQuestion | null {
  if (!question || question.turn_id !== turnId) return question ?? null;
  const answered = (question.answered ?? []).concat(index);
  if (answered.length >= question.questions.length) return null;
  return { ...question, answered };
}
