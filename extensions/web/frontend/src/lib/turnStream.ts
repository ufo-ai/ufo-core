import { BASE, getJson } from "@/lib/api";
import { holdTurn, moveTurnHold, releaseTurn } from "@/lib/appStatusStore";
import { money } from "@/lib/money";
import { tokens } from "@/lib/turnMeta";
import {
  chatState,
  liveTurn,
  migrateChat,
  updateChat,
  type ActivityEvent,
  type Bubble,
  type ChatTurn,
  type LiveTurn,
} from "@/lib/chatStore";
import type { ChatApp, ChatFile, SourceRef, SubagentRun, Transcript } from "@/lib/types";

const REATTACH_DELAYS_MS = [1_000, 2_000, 4_000, 8_000, 16_000, 30_000];
const MALFORMED_REPLY = "Malformed reply — try again.";
const RESUMED_NOTE = "Resumed after a restart";
const CANCELLED = "cancelled";
const STOPPED = "Stopped.";
const AUTO_MODEL = "auto";
const MODEL_HEADER = "x-ufo-model";

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

export function eventLabel(event: ActivityEvent, _phase: "active" | "done"): string {
  return event.text || "Completed a step.";
}

/** What the current step has read, each place once: a frame naming a page the step already drew
 *  adds nothing. A new tool step or the reply's first words start the list over, so the tiles are
 *  the step's, never stale. */
export function consulted(held: SourceRef[], items: SourceRef[]): SourceRef[] {
  const seen = new Set(held.map((source) => source.url || source.ref));
  const fresh = items.filter((source) => {
    const key = source.url || source.ref;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  return fresh.length ? held.concat(fresh) : held;
}

export function latestActivity(events: ActivityEvent[], runs: SubagentRun[]): string {
  const run = runs.at(-1);
  if (run) return latestActivity(run.events, run.subagents) || "Subagent · " + run.profile;
  const event = events.at(-1);
  return event ? eventLabel(event, "done") : "";
}

type RunFrame = {
  turn_id: string;
  parent_turn_id: string;
  conversation_id: string;
  profile: string;
  name: string;
  activity: string;
  status: string;
};

function runEvent(frame: RunFrame): ActivityEvent | null {
  return frame.activity ? { kind: "activity", text: frame.activity } : null;
}

function holdsRun(runs: SubagentRun[], turnId: string): boolean {
  return runs.some((run) => run.turn_id === turnId || holdsRun(run.subagents, turnId));
}

function advanceRun(run: SubagentRun, frame: RunFrame): SubagentRun {
  const event = runEvent(frame);
  return {
    ...run,
    ...(event ? { events: run.events.concat(event), current: event.text } : {}),
    ...(frame.status ? { running: false, current: undefined } : {}),
  };
}

export function applyRunFrame(
  runs: SubagentRun[],
  frame: RunFrame,
  ownerTurnId: string,
  ownerEvents: number,
): SubagentRun[] {
  if (frame.parent_turn_id !== ownerTurnId && holdsRun(runs, frame.parent_turn_id)) {
    return runs.map((run) =>
      run.turn_id === frame.parent_turn_id || holdsRun(run.subagents, frame.parent_turn_id)
        ? {
            ...run,
            subagents: applyRunFrame(
              run.subagents,
              frame,
              run.turn_id ?? "",
              run.events.length,
            ),
          }
        : run,
    );
  }
  if (!runs.some((run) => run.turn_id === frame.turn_id)) {
    const fresh: SubagentRun = {
      profile: frame.profile,
      name: frame.name,
      conversation_id: frame.conversation_id,
      events: [],
      output: "",
      subagents: [],
      turn_id: frame.turn_id,
      parent_turn_id: frame.parent_turn_id,
      running: true,
      at: ownerEvents,
    };
    return runs.concat(advanceRun(fresh, frame));
  }
  return runs.map((run) => (run.turn_id === frame.turn_id ? advanceRun(run, frame) : run));
}

/** A source opens with no cursor, so the turn's retained frames arrive from the first of them: whatever
 *  the chat had drawn belongs to the tail this one replaces, and holding it would glue two turns into one.
 *  The clock belongs to the turn rather than to the source, so a tail reopened on the same turn keeps it.
 *  The runs it left going are not that draft: they outlive their turn, so they stay as its own row. */
export function streamTurn(
  chatKey: string,
  turnId: string,
  answering: boolean,
  agentModel: string,
): void {
  REATTACHES.delete(chatKey);
  const leaving = chatState(chatKey).turn;
  const held =
    leaving && leaving.id !== turnId
      ? (chatState(chatKey).live?.subagents ?? []).filter((run) => run.running)
      : [];
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
    live: liveTurn(),
    turn: { id: turnId, answering },
  }));
  attach(chatKey, turnId, answering, false, agentModel);
}

function tailed(chatKey: string, turnId: string): boolean {
  return chatState(chatKey).turn?.id === turnId && SOURCES.has(chatKey);
}

function withoutWait(messages: Bubble[] | null): Bubble[] | null {
  if (messages === null) return null;
  return messages.map((message) =>
    message.arrival_id === undefined ? message : { ...message, arrival_id: undefined },
  );
}

/** One tail per chat, newest attach the writer. Two sources on one chat double every delta between
 *  them, and a reattach firing behind a live source opens a third. */
function attach(
  chatKey: string,
  turnId: string,
  answering: boolean,
  reattach: boolean,
  agentModel: string,
): void {
  const pending = TIMERS.get(chatKey);
  if (pending !== undefined) clearTimeout(pending);
  TIMERS.delete(chatKey);
  SOURCES.get(chatKey)?.close();
  let redrawOnOpen = reattach;
  const source = new EventSource(BASE + "/turns/" + turnId + "/stream");
  SOURCES.set(chatKey, source);

  const onLive = (change: (live: LiveTurn) => LiveTurn) =>
    updateChat(chatKey, (state) => ({ ...state, live: change(state.live ?? liveTurn()) }));

  const record = () =>
    updateChat(chatKey, (state) => {
      const live = state.live;
      const asked = state.handoffs.question;
      const question = asked && asked.turn_id === turnId ? asked : null;
      if (
        !live ||
        (!live.text &&
          !live.connect &&
          !live.subagents.length &&
          !live.files.length &&
          !live.apps.length &&
          question === null)
      ) {
        return state;
      }
      return {
        ...state,
        messages: (state.messages ?? []).concat({
          role: "assistant",
          text: live.text,
          ...(live.at ? { at: live.at } : {}),
          ...(live.summary ? { summary: live.summary } : {}),
          ...(live.connect ? { connect: live.connect } : {}),
          ...(live.events.length ? { events: live.events } : {}),
          ...(live.subagents.length ? { subagents: live.subagents } : {}),
          ...(live.files.length ? { files: live.files } : {}),
          ...(live.apps.length ? { apps: live.apps } : {}),
          ...(question ? { question } : {}),
        }),
      };
    });

  const release = () => {
    source.close();
    if (SOURCES.get(chatKey) === source) SOURCES.delete(chatKey);
  };

  const close = (ended: ChatTurn | null) => {
    release();
    releaseTurn(chatKey);
    updateChat(chatKey, (state) => ({
      ...state,
      busy: false,
      live: null,
      turn: null,
      ended,
      messages: withoutWait(state.messages),
    }));
  };

  source.addEventListener("open", () => {
    REATTACHES.delete(chatKey);
    if (redrawOnOpen) {
      redrawOnOpen = false;
      updateChat(chatKey, (state) => ({
        ...state,
        live: liveTurn(),
      }));
    } else {
      onLive((live) => ({ ...live, reconnecting: false }));
    }
  });

  source.onmessage = (event) => {
    const chunk = JSON.parse(event.data).text as string;
    onLive((live) => ({
      ...live,
      text: live.text + chunk,
      sources: live.sources.length ? [] : live.sources,
    }));
  };

  source.addEventListener("files", (event) => {
    const files = JSON.parse((event as MessageEvent).data).files as ChatFile[];
    onLive((live) => ({ ...live, files }));
  });

  source.addEventListener("apps", (event) => {
    const apps = JSON.parse((event as MessageEvent).data).apps as ChatApp[];
    onLive((live) => ({ ...live, apps }));
  });

  source.addEventListener("credentials", (event) => {
    const credentials = JSON.parse((event as MessageEvent).data);
    updateChat(chatKey, (state) => ({
      ...state,
      handoffs: { ...state.handoffs, credentials },
    }));
  });

  /** A frame whose id was already drawn is a replay after a reconnect, or a recovered turn republishing a
   *  round it recorded, and states its reply once. */
  source.addEventListener("reply", (event) => {
    const frame = JSON.parse((event as MessageEvent).data) as { id: string; text: string };
    updateChat(chatKey, (state) => {
      if (state.spoken.includes(frame.id) || !frame.text) return state;
      const messages = state.messages ?? [];
      const cut = messages.findIndex(
        (message) => message.arrival_id !== undefined || message.queued === true,
      );
      const at = cut === -1 ? messages.length : cut;
      const bubble: Bubble = { role: "assistant", text: frame.text };
      return {
        ...state,
        spoken: state.spoken.concat(frame.id),
        messages: [...messages.slice(0, at), bubble, ...messages.slice(at)],
      };
    });
  });

  source.addEventListener("comment", () => undefined);

  /** A drain that beats the send's response names a row no bubble is stamped with yet, so a bubble still
   *  `sending` clears the same way, and the drain consumes one marker per such row, in order. */
  source.addEventListener("absorbed", (event) => {
    const arrivals = JSON.parse((event as MessageEvent).data).arrivals as string[];
    updateChat(chatKey, (state) => {
      const fresh = arrivals.filter((id) => !state.absorbed.includes(id));
      const live = state.live;
      const steps: Bubble[] =
        fresh.length && live && live.events.length
          ? [
              {
                role: "assistant",
                text: "",
                ...(live.connect ? { connect: live.connect } : {}),
                events: live.events,
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
        messages: [...messages.slice(0, at), ...steps, ...messages.slice(at).map(folded)],
        live:
          live === null
            ? null
            : {
                ...liveTurn(),
                meter: live.meter,
                reconnecting: live.reconnecting,
                subagents: live.subagents.map((run) => ({ ...run, at: 0 })),
                files: live.files,
              },
      };
    });
  });

  source.addEventListener("activity", (event) => {
    const frame = JSON.parse((event as MessageEvent).data);
    const entry: ActivityEvent = { kind: "activity", text: frame.text };
    onLive((live) => ({
      ...live,
      events: live.events.concat(entry),
      activity: entry.text,
      sources: [],
    }));
  });

  source.addEventListener("sources", (event) => {
    const items = JSON.parse((event as MessageEvent).data).items as SourceRef[];
    onLive((live) => ({ ...live, sources: consulted(live.sources, items) }));
  });

  source.addEventListener("resumed", () => {
    const entry: ActivityEvent = { kind: "note", text: RESUMED_NOTE };
    onLive((live) => {
      const text = live.text.trim();
      return {
        ...live,
        text: "",
        events: text
          ? live.events.concat({ kind: "note", text }, entry)
          : live.events.concat(entry),
        activity: eventLabel(entry, "active"),
      };
    });
  });

  /** A background run publishes onto this stream while the parent writes its closing answer, and taking
   *  that answer into a step would leave the reply wordless until the terminal frame restored it. */
  source.addEventListener("subagent_activity", (event) => {
    const frame = JSON.parse((event as MessageEvent).data) as RunFrame;
    onLive((live) => ({
      ...live,
      subagents: applyRunFrame(live.subagents, frame, turnId, live.events.length),
    }));
  });

  source.addEventListener("subagent", (event) => {
    const run = JSON.parse((event as MessageEvent).data) as SubagentRun;
    onLive((live) => {
      const index = live.subagents.findIndex(
        (entry) => entry.conversation_id === run.conversation_id,
      );
      if (index === -1) return { ...live, subagents: live.subagents.concat(run) };
      const kept = live.subagents[index];
      const settled = kept.at === undefined ? run : { ...run, at: kept.at };
      return {
        ...live,
        subagents: [
          ...live.subagents.slice(0, index),
          settled,
          ...live.subagents.slice(index + 1),
        ],
      };
    });
  });

  source.addEventListener("cost", (event) => {
    const frame = JSON.parse((event as MessageEvent).data);
    onLive((live) => ({
      ...live,
      meter: [tokens(frame.tokens) + " tok", money(frame.cost_micro_usd)],
    }));
  });

  source.addEventListener("connect", (event) => {
    const frame = JSON.parse((event as MessageEvent).data);
    onLive((live) => ({ ...live, connect: frame }));
  });

  source.addEventListener("terminal", (event) => {
    const frame = JSON.parse((event as MessageEvent).data);
    updateChat(chatKey, (state) => {
      const live = state.live ?? liveTurn();
      const handoffs = { ...state.handoffs };
      let text = live.text;
      let summary = live.summary;
      if (frame.status === "done") {
        if (frame.text) text = frame.text;
        summary = {
          ...(agentModel === AUTO_MODEL ? {} : { model: frame.model }),
          tokens: frame.tokens,
          cost_micro_usd: frame.cost_micro_usd,
        };
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
      const at = frame.status === "done" ? new Date().toISOString() : live.at;
      return { ...state, handoffs, live: { ...live, text, summary, at } };
    });
    record();
    close("idle");
  });

  source.addEventListener("parked", (event) => {
    const message = JSON.parse((event as MessageEvent).data).message as string;
    onLive((live) => ({ ...live, text: live.text ? live.text + "\n" + message : message }));
    record();
    close("parked");
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
      close(null);
      return;
    }
    REATTACHES.set(chatKey, attempts);
    onLive((live) => ({ ...live, reconnecting: true }));
    TIMERS.set(
      chatKey,
      reattachTimer(
        () => attach(chatKey, turnId, answering, true, agentModel),
        REATTACH_DELAYS_MS[attempts - 1],
      ),
    );
  };
}

export function resyncChat(target: ChatTarget): void {
  const chatKey = target.key;
  const state = chatState(chatKey);
  if (state.turn) {
    const source = SOURCES.get(chatKey);
    if (source && source.readyState !== EventSource.CLOSED) return;
    attach(chatKey, state.turn.id, state.turn.answering, true, target.agentModel);
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
      earlierCursor: payload.earlier_cursor ?? null,
      fault: null,
      busy: running ? true : current.busy,
      turn: running ? { id: running, answering: false } : current.turn,
      live: running ? (current.live ?? liveTurn()) : current.live,
      handoffs: {
        question,
        credentials: ("credentials" in payload && payload.credentials) || null,
      },
    };
  });
  const streaming = chatState(chatKey).turn;
  if (streaming && !SOURCES.has(chatKey) && !TIMERS.has(chatKey)) {
    streamTurn(chatKey, streaming.id, streaming.answering, target.agentModel);
  }
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
    live: state.live ?? liveTurn(),
    messages: (state.messages ?? []).concat({
      role: "user",
      text: shown,
      at: new Date().toISOString(),
      sending: token,
      ...(attached.length ? { attached } : {}),
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
      headers: { ...timezoneHeader(), ...(pinned ? { [MODEL_HEADER]: pinned } : {}) },
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
  streamTurn(streamKey, accepted.turn_id, false, pinned ?? target.agentModel);
  return "accepted";
}

export async function answerQuestions(
  target: ChatTarget,
  turnId: string,
  answers: readonly { index: number; body: string }[],
): Promise<void> {
  const chatKey = target.key;
  const state = chatState(chatKey);
  if (!answers.length || state.busy || state.messages === null) return;
  bumpEpoch(chatKey);
  holdTurn(chatKey, target.agentId);
  updateChat(chatKey, (current) => ({ ...current, busy: true, ended: null, live: liveTurn() }));
  for (const { index, body } of answers) {
    let res: Response;
    try {
      res = await fetch(chatUrl(target), {
        method: "POST",
        body,
        credentials: "same-origin",
        headers: {
          "x-ufo-answer-turn": turnId,
          "x-ufo-answer-question": String(index),
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
    if (!(joined && tailed(chatKey, turn))) streamTurn(chatKey, turn, true, target.agentModel);
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
      if (outcome.turn_id) streamTurn(target.key, outcome.turn_id, false, target.agentModel);
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
  if (!(chatState(chatKey).turn !== null && SOURCES.has(chatKey))) releaseTurn(chatKey);
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
