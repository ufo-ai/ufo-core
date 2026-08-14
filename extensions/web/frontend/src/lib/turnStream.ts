import { BASE, getJson } from "@/lib/api";
import { money } from "@/lib/money";
import {
  chatState,
  liveTurn,
  migrateChat,
  updateChat,
  type ActivityEvent,
  type Bubble,
  type LiveTurn,
} from "@/lib/chatStore";
import type { ChatFile, SubagentRun, Transcript } from "@/lib/types";

const REATTACH_DELAYS_MS = [1_000, 2_000, 4_000, 8_000, 16_000, 30_000];
const MALFORMED_REPLY = "Malformed reply — try again.";
const CANCELLED = "cancelled";
const STOPPED = "Stopped.";

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

export function eventLabel(event: ActivityEvent, phase: "active" | "done"): string {
  switch (event.kind) {
    case "note":
      return event.text;
    case "skill":
      return (phase === "active" ? "Loading skill" : "Loaded skill") + " · " + event.name;
    case "tool":
      return event.description || (event.preview ? event.name + " " + event.preview : event.name);
  }
}

/** What the agent last did, reading to the deepest end of the tree — the line a collapsed
 *  disclosure states, so the member reads the newest work without opening it. */
export function latestActivity(events: ActivityEvent[], runs: SubagentRun[]): string {
  const run = runs.at(-1);
  if (run) return latestActivity(run.events, run.subagents) || "Subagent · " + run.profile;
  const event = events.at(-1);
  return event ? eventLabel(event, "done") : "";
}

/** Tail one turn, drawing its live bubble from what this tail replays. A source opens with no
 *  cursor, so the turn's retained frames arrive from the first of them: whatever the chat had drawn
 *  belongs to the tail this one replaces — the turn the page stopped following, or an earlier source
 *  on this same turn — and holding it would glue two turns into one bubble, or stand the reply the
 *  replay rebuilds behind a copy of itself. The files that tail listed go with it: the frame naming
 *  them arrives with the terminal, so the list under the log is the turn being tailed. A turn the
 *  page leaves is still the transcript's to state, on the next read of it. */
export function streamTurn(chatKey: string, turnId: string, answering: boolean): void {
  REATTACHES.delete(chatKey);
  updateChat(chatKey, (state) => ({
    ...state,
    live: liveTurn(),
    turn: { id: turnId, answering },
    handoffs: { ...state.handoffs, files: null },
  }));
  attach(chatKey, turnId, answering, false);
}

/** Whether this page already holds the tail of one turn. Its own bookkeeping about its own
 *  connection — never the answer to who owns the run, which is admission's to give. */
function tailed(chatKey: string, turnId: string): boolean {
  return chatState(chatKey).turn?.id === turnId && SOURCES.has(chatKey);
}

function withoutWait(messages: Bubble[] | null): Bubble[] | null {
  if (messages === null) return null;
  return messages.map((message) =>
    message.arrival_id === undefined ? message : { ...message, arrival_id: undefined },
  );
}

/** One tail per chat, newest attach the writer: whatever source the chat held is closed and a
 *  backoff reattach still pending is cleared before this one opens. Two sources on one chat double
 *  every delta between them, and a reattach firing behind a live source opens a third. */
function attach(chatKey: string, turnId: string, answering: boolean, reattach: boolean): void {
  const pending = TIMERS.get(chatKey);
  if (pending !== undefined) clearTimeout(pending);
  TIMERS.delete(chatKey);
  SOURCES.get(chatKey)?.close();
  let redrawOnOpen = reattach;
  const source = new EventSource(BASE + "/turns/" + turnId + "/stream");
  SOURCES.set(chatKey, source);

  const onLive = (change: (live: LiveTurn) => LiveTurn) =>
    updateChat(chatKey, (state) => ({ ...state, live: change(state.live ?? liveTurn()) }));

  /** The reply this turn settles into, carrying what the turn still asks of the member: the
   *  question stands under the words that asked it, so the reply is recorded for a question alone
   *  even when the turn wrote nothing else. */
  const record = () =>
    updateChat(chatKey, (state) => {
      const live = state.live;
      const asked = state.handoffs.question;
      const question = asked && asked.turn_id === turnId ? asked : null;
      if (!live || (!live.text && !live.subagents.length && question === null)) return state;
      return {
        ...state,
        messages: (state.messages ?? []).concat({
          role: "assistant",
          text: live.text,
          ...(live.meta ? { meta: live.meta } : {}),
          ...(live.connectUrl ? { connectUrl: live.connectUrl } : {}),
          ...(live.events.length ? { events: live.events } : {}),
          ...(live.subagents.length ? { subagents: live.subagents } : {}),
          ...(question ? { question } : {}),
        }),
      };
    });

  const release = () => {
    source.close();
    if (SOURCES.get(chatKey) === source) SOURCES.delete(chatKey);
  };

  /** The stream is over, so no message states a wait on it either. A row this turn never took up is
   *  still admitted and still pending, and the next transcript read draws the wait back from
   *  `queued_arrivals` — the projection that knows, rather than a pulse left standing under a
   *  bubble by a stream that ended. */
  const close = () => {
    release();
    updateChat(chatKey, (state) => ({
      ...state,
      busy: false,
      live: null,
      turn: null,
      messages: withoutWait(state.messages),
    }));
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
    updateChat(chatKey, (state) => ({ ...state, handoffs: { ...state.handoffs, files } }));
  });

  source.addEventListener("credentials", (event) => {
    const credentials = JSON.parse((event as MessageEvent).data);
    updateChat(chatKey, (state) => ({
      ...state,
      handoffs: { ...state.handoffs, credentials },
    }));
  });

  /** A drain is a round boundary: the text streamed before it is a finished reply the engine keeps
   *  in the window ahead of the messages it folded — not narration for the closing answer to
   *  replace — so it settles here, ahead of the bubble whose id the frame names, which is where the
   *  durable transcript will state it. A drain that beats the send's response names a row no bubble
   *  is stamped with yet, so a bubble still `sending` anchors the cut the same way — the queue row
   *  it committed is the fold's target whether or not the response is back — and the drain consumes
   *  one marker per such row, in order, since rows fold in the order the sends appended them: a
   *  marker left behind would anchor the next drain's reply above a message already answered. The
   *  live bubble then starts the next round empty. A frame whose ids were all seen is a replay of a
   *  round already recorded: it still ends the round, and recording it again would state the reply
   *  twice. */
  source.addEventListener("absorbed", (event) => {
    const arrivals = JSON.parse((event as MessageEvent).data).arrivals as string[];
    updateChat(chatKey, (state) => {
      const fresh = arrivals.filter((id) => !state.absorbed.includes(id));
      const live = state.live;
      const reply: Bubble[] =
        fresh.length && live && (live.text || live.subagents.length)
          ? [
              {
                role: "assistant",
                text: live.text,
                ...(live.connectUrl ? { connectUrl: live.connectUrl } : {}),
                ...(live.events.length ? { events: live.events } : {}),
                ...(live.subagents.length ? { subagents: live.subagents } : {}),
              },
            ]
          : [];
      const messages = state.messages ?? [];
      const cut = messages.findIndex(
        (message) =>
          (message.arrival_id !== undefined && arrivals.includes(message.arrival_id)) ||
          message.queued === true,
      );
      const at = cut === -1 ? messages.length : cut;
      let inFlight = fresh.filter(
        (id) => !messages.some((message) => message.arrival_id === id),
      ).length;
      const folded = (message: Bubble): Bubble => {
        if (message.arrival_id !== undefined && arrivals.includes(message.arrival_id)) {
          return { ...message, arrival_id: undefined };
        }
        if (message.queued === true && inFlight > 0) {
          inFlight -= 1;
          return { ...message, queued: undefined };
        }
        return message;
      };
      return {
        ...state,
        absorbed: state.absorbed.concat(fresh),
        messages: [...messages.slice(0, at), ...reply, ...messages.slice(at).map(folded)],
        live:
          live === null
            ? null
            : { ...liveTurn(), meter: live.meter, reconnecting: live.reconnecting },
      };
    });
  });

  source.addEventListener("tool", (event) => {
    const frame = JSON.parse((event as MessageEvent).data);
    const entry: ActivityEvent = {
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
    const entry: ActivityEvent = { kind: "skill", name: frame.skill, preview: "", description: "" };
    onLive((live) => ({
      ...live,
      events: live.events.concat(entry),
      activity: eventLabel(entry, "active"),
    }));
  });

  source.addEventListener("subagent", (event) => {
    const run = JSON.parse((event as MessageEvent).data) as SubagentRun;
    onLive((live) =>
      live.subagents.some((entry) => entry.conversation_id === run.conversation_id)
        ? live
        : { ...live, subagents: live.subagents.concat(run) },
    );
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
          (frame.status === CANCELLED
            ? STOPPED
            : "(" + frame.status + (frame.error_class ? ": " + frame.error_class : "") + ")");
        text = text ? text + "\n" + fallback : fallback;
        if (!answering) handoffs.question = null;
      }
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
    release();
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
  if (!result.ok) {
    updateChat(chatKey, (current) => {
      if (onlyIfEmpty && current.messages !== null) return current;
      if ((RESYNC_EPOCH.get(chatKey) ?? 0) !== epoch) return current;
      if (!onlyIfEmpty && (current.busy || current.turn)) return current;
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
    if (!onlyIfEmpty && (current.busy || current.turn)) return current;
    const question = current.handoffs.question ?? null;
    const running = ("turn" in payload && payload.turn) || null;
    return {
      ...current,
      messages: payload.messages,
      fault: null,
      busy: running ? true : current.busy,
      turn: running ? { id: running, answering: false } : current.turn,
      live: running ? (current.live ?? liveTurn()) : current.live,
      handoffs: {
        question,
        credentials: ("credentials" in payload && payload.credentials) || null,
        files: ("files" in payload && payload.files) || null,
      },
    };
  });
  const streaming = chatState(chatKey).turn;
  if (streaming && !SOURCES.has(chatKey) && !TIMERS.has(chatKey)) {
    streamTurn(chatKey, streaming.id, streaming.answering);
  }
}

/** Stop one running turn. The reply is the cancel landing, never the cancelled state — the turn's
 *  own tail delivers that terminal, as it does for a turn stopped from any other surface. A stop
 *  that did not land is reported, since the member pressed it and nothing else on the page says
 *  the turn is still going. */

/** Admit one message and tail what it landed on.
 *
 *  Whether this delivery opened the run it names is admission's answer, taken under the
 *  conversation-row lock and returned as `opened_run` — the same fact Slack gates its per-run
 *  reporters on. `opened_run` false beside an `arrival_id` is the one outcome that joined a run
 *  already open, and that run's frames arrive on the tail this page already holds; every other
 *  outcome is this delivery's to tail, a refusal included, since a refused message founds a turn
 *  whose own terminal the stream replays.
 *
 *  A message admitted while a turn runs also gets the queue row it joined: the bubble holds that id
 *  until the turn's `absorbed` event names it back. The drain can beat this response — the row is
 *  committed before the POST returns — so an id the stream has already named is never stamped on,
 *  or the wait would stand under the bubble with nothing left to clear it. */
export async function sendMessage(
  target: ChatTarget,
  body: string | FormData,
  shown: string,
): Promise<void> {
  const chatKey = target.key;
  // The one send that cannot be repeated: the `new` sentinel opens a conversation per request, so a
  // second send before the first answers founds a second conversation instead of joining the first,
  // and the two halves are answered apart. The founding send holds the chat busy until it lands.
  if (target.conversationId === null && chatState(chatKey).busy) return;
  bumpEpoch(chatKey);
  const token = String(++SENDS);
  updateChat(chatKey, (state) => ({
    ...state,
    busy: true,
    live: state.live ?? liveTurn(),
    messages: (state.messages ?? []).concat({
      role: "user",
      text: shown,
      sending: token,
      // A turn is already running, so this message waits for it rather than opening one: it belongs
      // under the reply that turn is streaming, which is where its drain will settle it. A send with
      // nothing running is the prompt a reply answers and stands above it.
      ...(state.turn !== null ? { queued: true } : {}),
    }),
  }));
  const settled = (key: string, arrivalId: string | null) =>
    updateChat(key, (state) => ({
      ...state,
      messages: (state.messages ?? []).map((message) =>
        message.sending === token
          ? {
              ...message,
              sending: undefined,
              queued: undefined,
              ...(arrivalId !== null && !state.absorbed.includes(arrivalId)
                ? { arrival_id: arrivalId }
                : {}),
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
    });
  } catch {
    settled(chatKey, null);
    failTurn(chatKey, "Network error — try again.");
    return;
  }
  if (!res.ok) {
    settled(chatKey, null);
    failTurn(chatKey, "Error " + res.status + " — try again.");
    return;
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
    return;
  }
  if (typeof accepted.turn_id !== "string") {
    settled(chatKey, null);
    failTurn(chatKey, MALFORMED_REPLY);
    return;
  }
  let streamKey = chatKey;
  if (target.conversationId) {
    target.onAccepted?.(target.conversationId);
  } else {
    if (typeof accepted.conversation_id !== "string" || typeof accepted.title !== "string") {
      settled(chatKey, null);
      failTurn(chatKey, MALFORMED_REPLY);
      return;
    }
    streamKey = accepted.conversation_id;
    migrateChat(chatKey, streamKey);
    target.onCreated?.(accepted.conversation_id, accepted.title);
  }
  const arrivalId = typeof accepted.arrival_id === "string" ? accepted.arrival_id : null;
  settled(streamKey, arrivalId);
  const joined = arrivalId !== null && accepted.opened_run === false;
  if (joined && tailed(streamKey, accepted.turn_id)) return;
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
    messages: markAnswered(current.messages, turnId, questionIndex, landed).concat({
      role: "user",
      text: landed,
    }),
  }));
  streamTurn(chatKey, turn, true);
}

/** End the turn the page is tailing. Nothing settles here: the stop admits no message, and the
 *  cancelled terminal the server publishes reaches the tail already open, so the turn comes down the
 *  way every other turn does. A stop that founds the next turn on a message the member had already
 *  sent names it in the answer, and the page moves its tail there — the waiting bubble settles off
 *  that turn's own frames. A refusal leaves the turn running with no field on the screen to answer
 *  it, so it reports as the chat's toast. */
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
      if (outcome.turn_id) streamTurn(target.key, outcome.turn_id, false);
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

/** A send that failed states its error in the log. Only a chat holding no live tail settles with
 *  it: the failure is the POST's alone, and a turn already streaming goes on — taking its bubble
 *  down would collapse the reply the member is reading under a send that never reached it. */
function failTurn(chatKey: string, message: string): void {
  updateChat(chatKey, (state) => {
    const tailing = state.turn !== null && SOURCES.has(chatKey);
    return {
      ...state,
      busy: tailing ? state.busy : false,
      live: tailing ? state.live : null,
      messages: (state.messages ?? []).concat({ role: "error", text: message }),
    };
  });
}

/** The words the surface confirmed it admitted, recorded on the entry they answer. The reply that
 *  asked holds the question, so the answer is written there too: the entry states what the member
 *  chose instead of disappearing, and a second submit has nothing left to send. */
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
