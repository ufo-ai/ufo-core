import { BASE, getJson } from "@/lib/api";
import { holdTurn, moveTurnHold, releaseTurn } from "@/lib/appStatusStore";
import {
  attachedTurn,
  chatState,
  migrateChat,
  updateChat,
  type ChatTurn,
} from "@/lib/chatStore";
import {
  EVENT_KINDS,
  decodeFrame,
  fold,
  land,
  liveTurn,
  lost,
  type Bubble,
  type EventKind,
} from "@/lib/turnRecord";
import type { Transcript } from "@/lib/types";

const REATTACH_DELAYS_MS = [1_000, 2_000, 4_000, 8_000, 16_000, 30_000];
const MALFORMED_REPLY = "Malformed reply — try again.";
const CONNECTION_LOST = "Connection lost — reload to see the reply.";
const MODEL_HEADER = "x-ufo-model";
const CLICK_HEADER = "x-ufo-click";
const CLICK_KIND_HEADER = "x-ufo-click-kind";
const STARTER_CLICK = "starter";
const THREAD_FOLLOWUP_CLICK = "thread-followup";

export const NEW_CONVERSATION = "new";

export type SendOutcome = "accepted" | "refused";

export type ChatTarget = {
  key: string;
  agentId: string;
  agentModel: string;
  conversationId: string | null;
  onCreated?: (conversationId: string, title: string) => void;
  onAccepted?: (conversationId: string) => void;
};

function chatUrl(target: Pick<ChatTarget, "agentId" | "conversationId">): string {
  const base = BASE + "/agents/" + target.agentId + "/chat";
  return base + "?conversation=" + (target.conversationId ?? NEW_CONVERSATION);
}

function transcriptPath(target: ChatTarget): string | null {
  if (!target.conversationId) return null;
  return "/agents/" + target.agentId + "/conversations/" + target.conversationId + "/transcript";
}

const RESYNC_EPOCH = new Map<string, number>();

let SENDS = 0;

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

/** A source opens with no cursor, so the turn's retained frames arrive from the first of them: whatever
 *  the chat had drawn belongs to the tail this one replaces, and holding it would glue two turns into one.
 *  The runs it left going are not that draft: they outlive their turn, so they stay as its own row. */
export function streamTurn(chatKey: string, turnId: string, agentModel: string): void {
  REATTACHES.delete(chatKey);
  const leaving = chatState(chatKey).live;
  const held =
    leaving !== null && leaving.id !== turnId ? leaving.runs.filter((run) => run.running) : [];
  updateChat(chatKey, (state) => ({
    ...state,
    ...(held.length
      ? {
          messages: (state.messages ?? []).concat({
            role: "assistant",
            text: "",
            subagents: held,
          }),
        }
      : {}),
    live: liveTurn(turnId, agentModel),
  }));
  attach(chatKey, turnId, false, agentModel);
}

function tailed(chatKey: string, turnId: string): boolean {
  return attachedTurn(chatState(chatKey)) === turnId && SOURCES.has(chatKey);
}

/** One tail per chat, newest attach the writer. Two sources on one chat double every delta between
 *  them, and a reattach firing behind a live source opens a third. */
function attach(chatKey: string, turnId: string, reattach: boolean, agentModel: string): void {
  const pending = TIMERS.get(chatKey);
  if (pending !== undefined) clearTimeout(pending);
  TIMERS.delete(chatKey);
  SOURCES.get(chatKey)?.close();
  let redrawOnOpen = reattach;
  const source = new EventSource(BASE + "/turns/" + turnId + "/stream");
  SOURCES.set(chatKey, source);

  const release = () => {
    source.close();
    if (SOURCES.get(chatKey) === source) SOURCES.delete(chatKey);
  };

  const close = (ended: ChatTurn | null, note: Bubble | null = null) => {
    release();
    releaseTurn(chatKey);
    updateChat(chatKey, (state) => {
      const landed =
        state.live === null ? (state.messages ?? []) : land(state.messages ?? [], state.live);
      return {
        ...state,
        busy: false,
        live: null,
        ended,
        messages: note === null ? landed : landed.concat(note),
      };
    });
  };

  const reconnecting = (value: boolean) =>
    updateChat(chatKey, (state) => ({
      ...state,
      live: { ...(state.live ?? liveTurn(turnId, agentModel)), reconnecting: value },
    }));

  source.addEventListener("open", () => {
    REATTACHES.delete(chatKey);
    if (redrawOnOpen) {
      redrawOnOpen = false;
      updateChat(chatKey, (state) => ({ ...state, live: liveTurn(turnId, agentModel) }));
    } else {
      reconnecting(false);
    }
  });

  const take = (kind: EventKind) => (event: Event) => {
    if (SOURCES.get(chatKey) !== source) return;
    const frame = decodeFrame(kind, (event as MessageEvent).data);
    if (frame === null) return;
    if (frame.kind === "credentials") {
      updateChat(chatKey, (state) => ({ ...state, credentials: frame.request }));
      return;
    }
    updateChat(chatKey, (state) => ({
      ...state,
      live: fold(state.live ?? liveTurn(turnId, agentModel), frame),
    }));
    if (frame.kind === "terminal") close("idle");
    else if (frame.kind === "parked") close("parked");
  };
  source.onmessage = take("message");
  for (const kind of EVENT_KINDS) {
    if (kind !== "message") source.addEventListener(kind, take(kind));
  }

  source.onerror = () => {
    if (source.readyState !== EventSource.CLOSED) {
      reconnecting(true);
      return;
    }
    release();
    const attempts = (REATTACHES.get(chatKey) ?? 0) + 1;
    if (attempts > REATTACH_DELAYS_MS.length) {
      updateChat(chatKey, (state) => ({
        ...state,
        live: state.live === null ? null : lost(state.live),
      }));
      close(null, { role: "error", text: CONNECTION_LOST });
      return;
    }
    REATTACHES.set(chatKey, attempts);
    reconnecting(true);
    TIMERS.set(
      chatKey,
      reattachTimer(
        () => attach(chatKey, turnId, true, agentModel),
        REATTACH_DELAYS_MS[attempts - 1],
      ),
    );
  };
}

export function resyncChat(target: ChatTarget): void {
  const chatKey = target.key;
  const state = chatState(chatKey);
  const streaming = attachedTurn(state);
  if (streaming !== null) {
    const source = SOURCES.get(chatKey);
    if (source && source.readyState !== EventSource.CLOSED) return;
    attach(chatKey, streaming, true, target.agentModel);
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
  if (!result.ok) {
    updateChat(chatKey, (current) => {
      if (onlyIfEmpty && current.messages !== null) return current;
      if ((RESYNC_EPOCH.get(chatKey) ?? 0) !== epoch) return current;
      if (!onlyIfEmpty && (current.busy || current.live !== null)) return current;
      return {
        ...current,
        fault: {
          title: "The conversation did not load.",
          description: result.message,
        },
      };
    });
    return;
  }
  const payload = result.payload;
  updateChat(chatKey, (current) => {
    if (onlyIfEmpty && current.messages !== null) return { ...current, fault: null };
    if ((RESYNC_EPOCH.get(chatKey) ?? 0) !== epoch) return current;
    if (!onlyIfEmpty && (current.busy || current.live !== null)) return current;
    const running = ("turn" in payload && payload.turn) || null;
    return {
      ...current,
      messages: payload.messages,
      earlierCursor: payload.earlier_cursor ?? null,
      fault: null,
      busy: running ? true : current.busy,
      live: running
        ? current.live !== null && current.live.id === running
          ? current.live
          : liveTurn(running, target.agentModel)
        : current.live,
      credentials: ("credentials" in payload && payload.credentials) || null,
    };
  });
  const streaming = attachedTurn(chatState(chatKey));
  if (streaming !== null && !SOURCES.has(chatKey) && !TIMERS.has(chatKey)) {
    streamTurn(chatKey, streaming, target.agentModel);
  }
}

/** The press a message came from, counted where the fleet can see it: the page holds no metric
 *  pipe of its own, so a click rides the POST it causes and the surface counts it there. */
function starterHeaders(starter: string | null): Record<string, string> {
  return starter === null
    ? {}
    : { [CLICK_HEADER]: STARTER_CLICK, [CLICK_KIND_HEADER]: starter };
}

function timezoneHeader(): Record<string, string> {
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return zone ? { "x-ufo-timezone": zone } : {};
}

/** The chat POST is the chat transport, so a thread is opened by admitting its first message and
 *  nothing else. Nothing is tailed and no chat key is held. */
export async function openConversation(agentId: string, text: string): Promise<string | null> {
  try {
    const res = await fetch(chatUrl({ agentId, conversationId: null }), {
      method: "POST",
      credentials: "same-origin",
      body: text,
      headers: timezoneHeader(),
    });
    if (!res.ok) return null;
    const founded: { conversation_id?: unknown } = await res.json();
    return typeof founded.conversation_id === "string" ? founded.conversation_id : null;
  } catch {
    return null;
  }
}

/** Whether this delivery opened the run it names is admission's answer, taken under the conversation-row
 *  lock. The drain can beat this response — the row is committed before the POST returns.
 *  `pinned` is the model the composer picked for this thread: it rides each message as the
 *  turn's own model and writes nothing. */
export async function sendMessage(
  target: ChatTarget,
  body: string | FormData,
  shown: string,
  attached: File[] = [],
  pinned: string | null = null,
  starter: string | null = null,
): Promise<SendOutcome> {
  const chatKey = target.key;
  // The `new` sentinel opens a conversation per request, so a second send before the first answers
  // founds a second conversation instead of joining it, and the two halves are answered apart.
  if (target.conversationId === null && chatState(chatKey).busy) {
    updateChat(chatKey, (state) => ({
      ...state,
      fault: {
        title: "The conversation is still opening.",
        description: "Send again once it has.",
      },
    }));
    return "refused";
  }
  bumpEpoch(chatKey);
  holdTurn(chatKey, target.agentId);
  const token = String(++SENDS);
  updateChat(chatKey, (state) => ({
    ...state,
    busy: true,
    ended: null,
    live: state.live ?? liveTurn(null, pinned ?? target.agentModel),
    messages: (state.messages ?? []).concat({
      role: "user",
      text: shown,
      at: new Date().toISOString(),
      sending: token,
      ...(attached.length ? { attached } : {}),
      ...(attachedTurn(state) !== null ? { queued: true } : {}),
    }),
  }));
  /** A row the turn has already taken up waits on nothing, and a turn that has ended drains nothing
   *  more, so the row is stamped only while a turn stands to take it. */
  const settled = (key: string, arrivalId: string | null) =>
    updateChat(key, (state) => ({
      ...state,
      messages: (state.messages ?? []).map((message) =>
        message.sending === token
          ? {
              ...message,
              sending: undefined,
              queued: undefined,
              ...(arrivalId !== null && state.live !== null ? { arrival_id: arrivalId } : {}),
            }
          : message,
      ),
    }));
  let res: Response;
  try {
    res = await fetch(chatUrl(target), {
      method: "POST",
      body,
      credentials: "same-origin",
      headers: {
        ...timezoneHeader(),
        ...starterHeaders(starter),
        ...(pinned ? { [MODEL_HEADER]: pinned } : {}),
      },
    });
  } catch {
    settled(chatKey, null);
    failTurn(chatKey, "Network error — try again.");
    return "accepted";
  }
  if (!res.ok) {
    settled(chatKey, null);
    failTurn(chatKey, "Error " + res.status + " — try again.");
    return "accepted";
  }
  let accepted: {
    turn_id?: unknown;
    conversation_id?: unknown;
    title?: unknown;
    arrival_id?: unknown;
    opened_run?: unknown;
  };
  try {
    accepted = await res.json();
  } catch {
    settled(chatKey, null);
    failTurn(chatKey, "Network error — try again.");
    return "accepted";
  }
  if (typeof accepted.turn_id !== "string") {
    settled(chatKey, null);
    failTurn(chatKey, MALFORMED_REPLY);
    return "accepted";
  }
  let streamKey = chatKey;
  if (target.conversationId) {
    target.onAccepted?.(target.conversationId);
  } else {
    if (typeof accepted.conversation_id !== "string" || typeof accepted.title !== "string") {
      settled(chatKey, null);
      failTurn(chatKey, MALFORMED_REPLY);
      return "accepted";
    }
    streamKey = accepted.conversation_id;
    moveTurnHold(chatKey, streamKey);
    migrateChat(chatKey, streamKey, accepted.title);
    target.onCreated?.(accepted.conversation_id, accepted.title);
  }
  const arrivalId = typeof accepted.arrival_id === "string" ? accepted.arrival_id : null;
  settled(streamKey, arrivalId);
  const joined = arrivalId !== null && accepted.opened_run === false;
  if (joined && tailed(streamKey, accepted.turn_id)) return "accepted";
  streamTurn(streamKey, accepted.turn_id, pinned ?? target.agentModel);
  return "accepted";
}

export async function answerQuestions(
  target: ChatTarget,
  turnId: string,
  answers: readonly { index: number; body: string; picked: boolean }[],
): Promise<void> {
  const chatKey = target.key;
  const state = chatState(chatKey);
  if (!answers.length || state.busy || state.messages === null) return;
  bumpEpoch(chatKey);
  holdTurn(chatKey, target.agentId);
  updateChat(chatKey, (current) => ({
    ...current,
    busy: true,
    ended: null,
    live: liveTurn(null, target.agentModel),
  }));
  for (const { index, body, picked } of answers) {
    let res: Response;
    try {
      res = await fetch(chatUrl(target), {
        method: "POST",
        body,
        credentials: "same-origin",
        headers: {
          "x-ufo-answer-turn": turnId,
          "x-ufo-answer-question": String(index),
          ...(picked ? { [CLICK_HEADER]: THREAD_FOLLOWUP_CLICK } : {}),
          ...timezoneHeader(),
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
    let payload: { body?: unknown; turn_id?: unknown; arrival_id?: unknown; opened_run?: unknown };
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
    const arrivalId = typeof payload.arrival_id === "string" ? payload.arrival_id : null;
    updateChat(chatKey, (current) => ({
      ...current,
      messages: markAnswered(current.messages, turnId, index, landed),
    }));
    const joined = arrivalId !== null && payload.opened_run === false;
    if (!(joined && tailed(chatKey, turn))) streamTurn(chatKey, turn, target.agentModel);
  }
}

export async function stopTurn(target: ChatTarget, turnId: string): Promise<void> {
  let description: string;
  try {
    const res = await fetch(chatUrl(target), {
      method: "POST",
      credentials: "same-origin",
      headers: { "x-ufo-stop-turn": turnId },
    });
    if (res.ok) {
      const outcome = (await res.json()) as { stopped: boolean; turn_id?: string };
      if (outcome.turn_id) streamTurn(target.key, outcome.turn_id, target.agentModel);
      return;
    }
    description = "Error " + res.status + " — try again.";
  } catch {
    description = "Network error — try again.";
  }
  updateChat(target.key, (state) => ({
    ...state,
    fault: { title: "The turn did not stop.", description },
  }));
}

function failTurn(chatKey: string, message: string): void {
  const tailing = attachedTurn(chatState(chatKey)) !== null && SOURCES.has(chatKey);
  if (!tailing) releaseTurn(chatKey);
  updateChat(chatKey, (state) => ({
    ...state,
    busy: tailing ? state.busy : false,
    live: tailing ? state.live : null,
    messages: (state.messages ?? []).concat({ role: "error", text: message }),
  }));
}

function markAnswered(
  messages: Bubble[] | null,
  turnId: string,
  index: number,
  body: string,
): Bubble[] {
  return (messages ?? []).map((message) =>
    message.question?.turn_id === turnId
      ? {
          ...message,
          question: {
            ...message.question,
            answered: { ...message.question.answered, [index]: body },
          },
        }
      : message,
  );
}
