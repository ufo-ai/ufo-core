import { BASE, getJson } from "@/lib/api";
import { money } from "@/lib/money";
import { chatState, liveTurn, updateChat, type LiveTurn, type ToolEvent } from "@/lib/chatStore";
import type { ChatFile, ChatQuestion, Transcript } from "@/lib/types";

const REATTACH_DELAYS_MS = [1_000, 2_000, 4_000, 8_000, 16_000, 30_000];

export function tokens(count: number): string {
  return count.toLocaleString("en-US");
}

const RESYNC_EPOCH = new Map<string, number>();

function bumpEpoch(agentId: string): void {
  RESYNC_EPOCH.set(agentId, (RESYNC_EPOCH.get(agentId) ?? 0) + 1);
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

export function streamTurn(agentId: string, turnId: string, answering: boolean): void {
  REATTACHES.delete(agentId);
  updateChat(agentId, (state) => ({ ...state, turn: { id: turnId, answering } }));
  attach(agentId, turnId, answering, false);
}

function attach(agentId: string, turnId: string, answering: boolean, reattach: boolean): void {
  TIMERS.delete(agentId);
  let redrawOnOpen = reattach;
  const source = new EventSource(BASE + "/turns/" + turnId + "/stream");
  SOURCES.set(agentId, source);
  let sawFiles = false;

  const onLive = (change: (live: LiveTurn) => LiveTurn) =>
    updateChat(agentId, (state) => ({ ...state, live: change(state.live ?? liveTurn()) }));

  const record = () =>
    updateChat(agentId, (state) => {
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
    SOURCES.delete(agentId);
    updateChat(agentId, (state) => ({ ...state, busy: false, live: null, turn: null }));
  };

  source.addEventListener("open", () => {
    REATTACHES.delete(agentId);
    if (redrawOnOpen) {
      redrawOnOpen = false;
      updateChat(agentId, (state) => ({ ...state, live: liveTurn() }));
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
    updateChat(agentId, (state) => ({
      ...state,
      handoffs: { ...state.handoffs, files },
      live: { ...(state.live ?? liveTurn()), files },
    }));
  });

  source.addEventListener("credentials", (event) => {
    const credentials = JSON.parse((event as MessageEvent).data);
    updateChat(agentId, (state) => ({
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
    updateChat(agentId, (state) => {
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
    SOURCES.delete(agentId);
    const attempts = (REATTACHES.get(agentId) ?? 0) + 1;
    if (attempts > REATTACH_DELAYS_MS.length) {
      record();
      updateChat(agentId, (state) => ({
        ...state,
        messages: (state.messages ?? []).concat({
          role: "error",
          text: "Connection lost — reload to see the reply.",
        }),
      }));
      close();
      return;
    }
    REATTACHES.set(agentId, attempts);
    onLive((live) => ({ ...live, reconnecting: true }));
    TIMERS.set(
      agentId,
      reattachTimer(() => attach(agentId, turnId, answering, true), REATTACH_DELAYS_MS[attempts - 1]),
    );
  };
}

export function resyncChat(agentId: string): void {
  const state = chatState(agentId);
  if (state.turn) {
    const source = SOURCES.get(agentId);
    if (source && source.readyState !== EventSource.CLOSED) return;
    const timer = TIMERS.get(agentId);
    if (timer !== undefined) clearTimeout(timer);
    attach(agentId, state.turn.id, state.turn.answering, true);
    return;
  }
  if (state.busy || state.messages === null) return;
  void refreshTranscript(agentId);
}

export async function refreshTranscript(agentId: string, onlyIfEmpty = false): Promise<void> {
  if (onlyIfEmpty && chatState(agentId).messages !== null) return;
  const epoch = RESYNC_EPOCH.get(agentId) ?? 0;
  const result = await getJson<Transcript>("/agents/" + agentId + "/transcript");
  if (!result.ok && !onlyIfEmpty) return;
  const payload: Transcript = result.ok ? result.payload : { messages: [] };
  updateChat(agentId, (current) => {
    if (onlyIfEmpty && current.messages !== null) return current;
    if ((RESYNC_EPOCH.get(agentId) ?? 0) !== epoch) return current;
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
  agentId: string,
  body: string | FormData,
  shown: string,
): Promise<void> {
  bumpEpoch(agentId);
  updateChat(agentId, (state) => ({
    ...state,
    busy: true,
    live: liveTurn(),
    messages: (state.messages ?? []).concat({ role: "user", text: shown }),
  }));
  let res: Response;
  try {
    res = await fetch(BASE + "/agents/" + agentId + "/chat", {
      method: "POST",
      body,
      credentials: "same-origin",
    });
  } catch {
    failTurn(agentId, "Network error — try again.");
    return;
  }
  if (!res.ok) {
    failTurn(agentId, "Error " + res.status + " — try again.");
    return;
  }
  let accepted: { turn_id: string };
  try {
    accepted = await res.json();
  } catch {
    failTurn(agentId, "Network error — try again.");
    return;
  }
  streamTurn(agentId, accepted.turn_id, false);
}

export async function answerQuestion(
  agentId: string,
  turnId: string,
  questionIndex: number,
  body: string,
): Promise<void> {
  const state = chatState(agentId);
  if (state.busy || state.messages === null) return;
  bumpEpoch(agentId);
  updateChat(agentId, (current) => ({ ...current, busy: true, live: liveTurn() }));
  let res: Response;
  try {
    res = await fetch(BASE + "/agents/" + agentId + "/chat", {
      method: "POST",
      body,
      credentials: "same-origin",
      headers: {
        "x-ufo-answer-turn": turnId,
        "x-ufo-answer-question": String(questionIndex),
      },
    });
  } catch {
    failTurn(agentId, "Network error — try again.");
    return;
  }
  if (!res.ok) {
    failTurn(agentId, "Error " + res.status + " — try again.");
    return;
  }
  let payload: { body?: string; turn_id: string };
  try {
    payload = await res.json();
  } catch {
    failTurn(agentId, "Network error — try again.");
    return;
  }
  const landed = payload.body || body;
  updateChat(agentId, (current) => ({
    ...current,
    messages: (current.messages ?? []).concat({ role: "user", text: landed }),
    handoffs: {
      ...current.handoffs,
      question: markAnswered(current.handoffs.question, questionIndex, turnId),
    },
  }));
  streamTurn(agentId, payload.turn_id, true);
}

function failTurn(agentId: string, message: string): void {
  updateChat(agentId, (state) => ({
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
