import type {
  ActivityEvent,
  SourceRef,
  Step,
  SubagentRun,
  TerminalFrame,
  TurnRecord,
} from "@/lib/contract";
import { fault } from "@/lib/rum";
import type {
  ChatApp,
  ChatConnect,
  ChatFile,
  CredentialRequest,
  Message,
  TurnSummary,
} from "@/lib/types";

/** Every event the chat stream delivers, the web surface's `_sse` and `_event` names held equal to
 *  this list by `gates.py`. */
export const EVENT_KINDS = [
  "message",
  "activity",
  "sources",
  "subagent_activity",
  "subagent",
  "reply",
  "comment",
  "absorbed",
  "resumed",
  "cost",
  "files",
  "apps",
  "connect",
  "credentials",
  "terminal",
  "parked",
] as const;

export type EventKind = (typeof EVENT_KINDS)[number];

/** `SILENCE_SENTINEL` in `core/src/ufo/runtime/ext/surface.py`, held equal by `gates.py`. */
export const SILENCE_SENTINEL = "<response></response>";

const SILENCE_NAME = SILENCE_SENTINEL.slice(1, SILENCE_SENTINEL.indexOf(">"));
const SILENT = new RegExp(
  "^(?:<" + SILENCE_NAME + ">\\s*</" + SILENCE_NAME + ">|<" + SILENCE_NAME + "\\s*/>)$",
);
const BARE_BREAK = /^<br\s*\/?>$/i;

export const RESUMED_NOTE = "Resumed after a restart";
export const RESEARCHING = "Researching…";
export const RECONNECTING = "Reconnecting…";
const CANCELLED = "cancelled";
const STOPPED = "Stopped.";
const AUTO_MODEL = "auto";
const DONE = "done";
const STATUSES: readonly TerminalFrame["status"][] = ["done", "failed", "cancelled"];

export type RunFrame = {
  turn_id: string;
  parent_turn_id: string;
  conversation_id: string;
  profile: string;
  name: string;
  activity: string;
  status: string;
};

export type Frame =
  | { kind: "message"; text: string }
  | { kind: "activity"; text: string; call_id: string }
  | { kind: "sources"; items: SourceRef[] }
  | { kind: "subagent_activity"; run: RunFrame }
  | { kind: "subagent"; run: SubagentRun }
  | { kind: "reply"; id: string; text: string }
  | { kind: "comment"; id: string; text: string }
  | { kind: "absorbed"; arrivals: string[] }
  | { kind: "resumed"; attempt: string }
  | { kind: "cost"; tokens: number; cost_micro_usd: number }
  | { kind: "files"; files: ChatFile[] }
  | { kind: "apps"; apps: ChatApp[] }
  | { kind: "connect"; connect: ChatConnect }
  | { kind: "credentials"; request: CredentialRequest }
  | { kind: "terminal"; frame: TerminalFrame; at: string }
  | { kind: "parked"; message: string };

/** A credentials frame is the conversation's handoff, not the turn's; every other frame folds into
 *  the turn record. */
export type TurnFrame = Exclude<Frame, { kind: "credentials" }>;

type Fields = Record<string, unknown>;

const isFields = (value: unknown): value is Fields =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const str = (value: unknown): value is string => typeof value === "string";
const num = (value: unknown): value is number => typeof value === "number";
const list = (value: unknown, of: (item: unknown) => boolean): value is unknown[] =>
  Array.isArray(value) && value.every(of);

/** The frame an SSE event carries, or null with a fault logged: an event name the stream does not
 *  declare, or a payload without the fields its kind requires, reaches nothing downstream. */
export function decodeFrame(
  kind: string,
  data: string,
  at: string = new Date().toISOString(),
): Frame | null {
  if (!(EVENT_KINDS as readonly string[]).includes(kind)) {
    fault("frame.unknown", { kind });
    return null;
  }
  let payload: unknown;
  try {
    payload = JSON.parse(data);
  } catch {
    fault("frame.malformed", { kind, data });
    return null;
  }
  const frame = isFields(payload) ? shape(kind as EventKind, payload, at) : null;
  if (frame === null) fault("frame.malformed", { kind, data });
  return frame;
}

function shape(kind: EventKind, fields: Fields, at: string): Frame | null {
  switch (kind) {
    case "message":
      return str(fields.text) ? { kind, text: fields.text } : null;
    case "activity":
      return str(fields.text)
        ? { kind, text: fields.text, call_id: str(fields.call_id) ? fields.call_id : "" }
        : null;
    case "sources":
      return list(fields.items, isFields) ? { kind, items: fields.items as SourceRef[] } : null;
    case "subagent_activity":
      return str(fields.turn_id) &&
        str(fields.parent_turn_id) &&
        str(fields.conversation_id) &&
        str(fields.profile)
        ? {
            kind,
            run: {
              turn_id: fields.turn_id,
              parent_turn_id: fields.parent_turn_id,
              conversation_id: fields.conversation_id,
              profile: fields.profile,
              name: str(fields.name) ? fields.name : "",
              activity: str(fields.activity) ? fields.activity : "",
              status: str(fields.status) ? fields.status : "",
            },
          }
        : null;
    case "subagent":
      return str(fields.profile) &&
        str(fields.conversation_id) &&
        Array.isArray(fields.events) &&
        Array.isArray(fields.subagents)
        ? {
            kind,
            run: { ...(fields as unknown as SubagentRun), output: str(fields.output) ? fields.output : "" },
          }
        : null;
    case "reply":
    case "comment":
      return str(fields.id) ? { kind, id: fields.id, text: str(fields.text) ? fields.text : "" } : null;
    case "absorbed":
      return list(fields.arrivals, str) ? { kind, arrivals: fields.arrivals as string[] } : null;
    case "resumed":
      return { kind, attempt: str(fields.attempt) ? fields.attempt : "" };
    case "cost":
      return num(fields.tokens) && num(fields.cost_micro_usd)
        ? { kind, tokens: fields.tokens, cost_micro_usd: fields.cost_micro_usd }
        : null;
    case "files":
      return list(fields.files, isFields) ? { kind, files: fields.files as ChatFile[] } : null;
    case "apps":
      return list(fields.apps, isFields) ? { kind, apps: fields.apps as ChatApp[] } : null;
    case "connect":
      return { kind, connect: fields as ChatConnect };
    case "credentials":
      return str(fields.sealed) && str(fields.reason) && Array.isArray(fields.prompts)
        ? { kind, request: fields as unknown as CredentialRequest }
        : null;
    case "terminal":
      return (STATUSES as readonly unknown[]).includes(fields.status)
        ? { kind, at, frame: fields as unknown as TerminalFrame }
        : null;
    case "parked":
      return str(fields.message) ? { kind, message: fields.message } : null;
  }
}

type TextStep = Extract<Step, { kind: "text" }>;
type DrainStep = Extract<Step, { kind: "drain" }>;
type TurnEnd = NonNullable<TurnRecord["end"]>;

/** The record as this page watches it: the contract's turn record, plus what the web surface adds
 *  at the terminal — the files, apps and connect act it draws — and what only this page knows:
 *  the model the turn was asked to run on, and whether its stream is reconnecting. */
export type LiveTurn = TurnRecord & {
  model: string;
  files: ChatFile[];
  apps: ChatApp[];
  connect: ChatConnect | null;
  reconnecting: boolean;
};

export function liveTurn(id: string | null = null, model = ""): LiveTurn {
  return {
    id,
    model,
    steps: [],
    runs: [],
    files: [],
    apps: [],
    connect: null,
    meter: null,
    reconnecting: false,
    end: null,
  };
}

/** What the current step has read, each place once: a frame naming a page the step already drew
 *  adds nothing. */
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
  return event ? event.text || "Completed a step." : "";
}

function holdsRun(runs: SubagentRun[], turnId: string): boolean {
  return runs.some((run) => run.turn_id === turnId || holdsRun(run.subagents, turnId));
}

function advanceRun(run: SubagentRun, frame: RunFrame): SubagentRun {
  return {
    ...run,
    ...(frame.activity
      ? { events: run.events.concat({ kind: "activity", text: frame.activity }), current: frame.activity }
      : {}),
    ...(frame.status ? { running: false, current: undefined } : {}),
  };
}

export function applyRunFrame(
  runs: SubagentRun[],
  frame: RunFrame,
  ownerTurnId: string | null,
): SubagentRun[] {
  if (frame.parent_turn_id !== ownerTurnId && holdsRun(runs, frame.parent_turn_id)) {
    return runs.map((run) =>
      run.turn_id === frame.parent_turn_id || holdsRun(run.subagents, frame.parent_turn_id)
        ? { ...run, subagents: applyRunFrame(run.subagents, frame, run.turn_id ?? null) }
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
    };
    return runs.concat(advanceRun(fresh, frame));
  }
  return runs.map((run) => (run.turn_id === frame.turn_id ? advanceRun(run, frame) : run));
}

function closed(steps: Step[]): Step[] {
  return steps.map((step) => ("open" in step && step.open ? { ...step, open: false } : step));
}

/** The text step the next words extend: the newest step once the replies delivered beside it are
 *  looked past, when that step is text still open. */
function openText(steps: Step[]): number {
  for (let index = steps.length - 1; index >= 0; index -= 1) {
    const step = steps[index];
    if (step.kind === "reply" || step.kind === "comment") continue;
    return step.kind === "text" && step.open ? index : -1;
  }
  return -1;
}

function withStep(record: LiveTurn, index: number, step: Step): LiveTurn {
  return { ...record, steps: record.steps.map((held, at) => (at === index ? step : held)) };
}

function opening(record: LiveTurn, step: Step): LiveTurn {
  return { ...record, steps: closed(record.steps).concat(step) };
}

/** The record after one frame. A frame after the turn's end, a run naming a parent the record does
 *  not hold, and a reply with no words are faults: logged, and the record stands as it was. */
export function fold(record: LiveTurn, frame: TurnFrame): LiveTurn {
  if (record.end !== null) {
    fault("frame.after_end", { turn: record.id, kind: frame.kind, end: record.end.kind });
    return record;
  }
  switch (frame.kind) {
    case "message": {
      if (!frame.text) return record;
      const at = openText(record.steps);
      if (at !== -1) {
        const held = record.steps[at] as TextStep;
        return withStep(record, at, { ...held, text: held.text + frame.text });
      }
      return opening(record, { kind: "text", text: frame.text, open: true });
    }
    case "activity":
      if (!frame.text) return record;
      return opening(record, {
        kind: "tool",
        label: frame.text,
        call_id: frame.call_id,
        sources: [],
        open: true,
      });
    case "sources": {
      const last = record.steps.at(-1);
      if (last?.kind === "tool" && last.open) {
        return withStep(record, record.steps.length - 1, {
          ...last,
          sources: consulted(last.sources, frame.items),
        });
      }
      return opening(record, {
        kind: "tool",
        label: null,
        call_id: "",
        sources: consulted([], frame.items),
        open: true,
      });
    }
    case "subagent_activity": {
      const run = frame.run;
      if (run.parent_turn_id !== record.id && !holdsRun(record.runs, run.parent_turn_id)) {
        fault("run.orphan", { turn: record.id, run: run.turn_id, parent: run.parent_turn_id });
      }
      return { ...record, runs: applyRunFrame(record.runs, run, record.id) };
    }
    case "subagent": {
      const at = record.runs.findIndex((run) => run.conversation_id === frame.run.conversation_id);
      return {
        ...record,
        runs:
          at === -1
            ? record.runs.concat(frame.run)
            : record.runs.map((run, index) => (index === at ? frame.run : run)),
      };
    }
    case "reply":
    case "comment": {
      if (!frame.text) {
        fault("reply.wordless", { turn: record.id, kind: frame.kind, id: frame.id });
        return record;
      }
      const held = record.steps.some(
        (step) => (step.kind === "reply" || step.kind === "comment") && step.id === frame.id,
      );
      if (held) return record;
      return { ...record, steps: record.steps.concat({ kind: frame.kind, id: frame.id, text: frame.text }) };
    }
    case "absorbed": {
      const drained = new Set(drains(record.steps).flatMap((step) => step.arrivals));
      const fresh = frame.arrivals.filter((id) => !drained.has(id));
      if (!fresh.length) return record;
      return opening(record, { kind: "drain", arrivals: fresh });
    }
    case "resumed":
      return opening(record, { kind: "resumed", attempt: frame.attempt });
    case "cost":
      return { ...record, meter: { tokens: frame.tokens, cost_micro_usd: frame.cost_micro_usd } };
    case "files":
      return { ...record, files: frame.files };
    case "apps":
      return { ...record, apps: frame.apps };
    case "connect":
      return { ...record, connect: frame.connect };
    case "terminal":
      return {
        ...record,
        steps: closed(record.steps),
        end: { kind: "terminal", frame: frame.frame, at: frame.at },
      };
    case "parked":
      return { ...record, steps: closed(record.steps), end: { kind: "parked", message: frame.message } };
  }
}

/** A stream that dropped for good ends the record without a verdict: the words it streamed stand,
 *  and the server's turn outlives the drop. */
export function lost(record: LiveTurn): LiveTurn {
  return { ...record, steps: closed(record.steps), end: { kind: "lost" } };
}

function drains(steps: Step[]): DrainStep[] {
  return steps.filter((step): step is DrainStep => step.kind === "drain");
}

/** The steps between drains: a drain is a round boundary a member's message crossed, and each side
 *  of it settles as its own reply. */
function segments(steps: Step[]): Step[][] {
  const held: Step[][] = [[]];
  for (const step of steps) {
    if (step.kind === "drain") held.push([]);
    else held[held.length - 1].push(step);
  }
  return held;
}

function texts(segment: Step[]): TextStep[] {
  return segment.filter((step): step is TextStep => step.kind === "text");
}

/** The text step the answer streamed into: the segment's last step, replies looked past. */
function closingText(segment: Step[]): TextStep | null {
  for (let index = segment.length - 1; index >= 0; index -= 1) {
    const step = segment[index];
    if (step.kind === "reply" || step.kind === "comment") continue;
    return step.kind === "text" ? step : null;
  }
  return null;
}

function eventsOf(segment: Step[], noted: (step: TextStep) => boolean): ActivityEvent[] {
  const events: ActivityEvent[] = [];
  for (const step of segment) {
    switch (step.kind) {
      case "text":
        if (noted(step) && step.text.trim()) events.push({ kind: "note", text: step.text.trim() });
        break;
      case "tool":
        if (step.label !== null) events.push({ kind: "activity", text: step.label });
        break;
      case "resumed":
        events.push({ kind: "note", text: RESUMED_NOTE });
        break;
      case "reply":
      case "comment":
      case "drain":
        break;
    }
  }
  return events;
}

function replies(segment: Step[]): Message[] {
  return segment.flatMap((step) =>
    step.kind === "reply" ? [{ role: "assistant", text: step.text }] : [],
  );
}

/** Each segment a drain closed, as the transcript states it back: the replies it delivered, then
 *  one wordless reply carrying its steps. */
function closedSegments(record: LiveTurn): Message[][] {
  return segments(record.steps)
    .slice(0, -1)
    .map((segment) => {
      const events = eventsOf(segment, () => true);
      return replies(segment).concat(events.length ? [{ role: "assistant", text: "", events }] : []);
    });
}

function fallback(end: TurnEnd | null): string | null {
  if (end === null || end.kind === "lost") return null;
  if (end.kind === "parked") return end.message;
  const frame = end.frame;
  if (frame.status === DONE) return null;
  if (frame.text) return frame.text;
  if (frame.status === CANCELLED) return STOPPED;
  return "(" + frame.status + (frame.error_class ? ": " + frame.error_class : "") + ")";
}

function doneFrame(end: TurnEnd | null): TerminalFrame | null {
  return end?.kind === "terminal" && end.frame.status === DONE ? end.frame : null;
}

/** The last segment's replies and its answering reply. A done terminal's text is the answer and
 *  the passages before it are notes; otherwise the passages are, closed by the end's own line. */
function lastSegment(record: LiveTurn): { replies: Message[]; answer: Message | null } {
  const segment = segments(record.steps).at(-1) ?? [];
  const done = doneFrame(record.end);
  const closing = closingText(segment);
  const stated = done?.text ? done.text : null;
  const streamed = texts(segment)
    .map((step) => step.text)
    .join("\n\n");
  const closer = fallback(record.end);
  const text =
    stated ?? (closer === null ? streamed : streamed ? streamed + "\n" + closer : closer);
  const events = eventsOf(segment, (step) => stated !== null && step !== closing);
  const question =
    done?.question && record.id !== null ? { turn_id: record.id, ...done.question } : null;
  const summary: TurnSummary | null =
    done === null
      ? null
      : {
          ...(record.model === AUTO_MODEL ? {} : { model: done.model ?? "" }),
          ...(done.tokens !== undefined ? { tokens: done.tokens } : {}),
          ...(done.cost_micro_usd !== undefined ? { cost_micro_usd: done.cost_micro_usd } : {}),
        };
  const empty =
    !text &&
    record.connect === null &&
    !record.runs.length &&
    !record.files.length &&
    !record.apps.length &&
    question === null;
  const answer: Message | null = empty
    ? null
    : {
        role: "assistant",
        text,
        ...(record.id !== null ? { turn: record.id } : {}),
        ...(done !== null && record.end?.kind === "terminal" ? { at: record.end.at } : {}),
        ...(summary ? { summary } : {}),
        ...(record.connect ? { connect: record.connect } : {}),
        ...(events.length ? { events } : {}),
        ...(record.runs.length ? { subagents: record.runs } : {}),
        ...(record.files.length ? { files: record.files } : {}),
        ...(record.apps.length ? { apps: record.apps } : {}),
        ...(question ? { question } : {}),
      };
  return { replies: replies(segment), answer };
}

/** The record as the transcript read states it back, so a turn watched live and one read after a
 *  reload are one shape. */
export function settled(record: LiveTurn): Message[] {
  const last = lastSegment(record);
  return [...closedSegments(record).flat(), ...last.replies, ...(last.answer ? [last.answer] : [])];
}

export type Answer = { kind: "words"; text: string } | { kind: "silence" } | { kind: "none" };

/** What a reply's text says: words, the sentinel a turn writes when it has nothing to say, or
 *  nothing at all. */
export function answerOf(text: string): Answer {
  const trimmed = text.trim();
  if (!trimmed) return { kind: "none" };
  return SILENT.test(trimmed) || BARE_BREAK.test(trimmed) ? { kind: "silence" } : { kind: "words", text };
}

/** A settled message with what this page alone knows of it: the connect act it left, the send it
 *  rode, the files the member picked, and whether it went out while a turn already ran. */
export type Bubble = Message & {
  connect?: ChatConnect;
  sending?: string;
  attached?: File[];
  queued?: boolean;
};

export type LiveRow = {
  kind: "live";
  key: string;
  record: LiveTurn;
  /** The step line's words, before the runs the turn waits on are counted over it. */
  working: string | null;
  sources: SourceRef[];
  waiting: SubagentRun[];
  body: string;
  /** The steps of the current segment the row draws nothing of. */
  folded: Step[];
};

export type Row = { kind: "said"; key: string; message: Bubble } | LiveRow;

function midTurn(message: Bubble): boolean {
  return message.arrival_id !== undefined || message.queued === true;
}

/** Which drain took each mid-turn message up, or -1 while none has. A send still awaiting its
 *  response carries no arrival id, so it claims, in order, a drained arrival no message carries. */
function drainedAt(later: Bubble[], record: LiveTurn): number[] {
  const held = drains(record.steps);
  const named = new Map<string, number>();
  held.forEach((drain, index) => drain.arrivals.forEach((id) => named.set(id, index)));
  const carried = new Set(later.flatMap((message) => message.arrival_id ?? []));
  const unclaimed = held.flatMap((drain, index) =>
    drain.arrivals.filter((id) => !carried.has(id)).map(() => index),
  );
  return later.map((message) => {
    if (message.arrival_id !== undefined) return named.get(message.arrival_id) ?? -1;
    if (message.queued === true) return unclaimed.shift() ?? -1;
    return -1;
  });
}

function taken(message: Bubble): Bubble {
  return { ...message, arrival_id: undefined, queued: undefined };
}

/** The live row's facts. The step line is the segment's last labelled step, or the note a resume
 *  left, or the opening word while nothing has streamed; the tiles are the open step's alone. */
function liveRow(record: LiveTurn, key: string): LiveRow {
  const segment = segments(record.steps).at(-1) ?? [];
  const passages = texts(segment);
  const body = passages.map((step) => step.text).join("\n\n");
  const last = segment.at(-1);
  const shown = [...segment]
    .reverse()
    .find((step) => (step.kind === "tool" && step.label !== null) || step.kind === "resumed");
  const label = shown === undefined ? null : shown.kind === "tool" ? shown.label : RESUMED_NOTE;
  const open = last?.kind === "tool" && last.open ? last : null;
  const working = record.reconnecting ? RECONNECTING : label ?? (body ? null : RESEARCHING);
  const drawn = new Set<Step>([...passages, ...(shown ? [shown] : []), ...(open ? [open] : [])]);
  return {
    kind: "live",
    key,
    record,
    working,
    sources: open?.sources ?? [],
    waiting: record.runs.filter((run) => run.running === true),
    body,
    folded: segment.filter((step) => step.kind !== "reply" && !drawn.has(step)),
  };
}

/** A reply whose whole answer is the silence sentinel and which carries nothing else: the turn ran
 *  and had nothing to say, and the log draws no row for it. */
export function silent(message: Message): boolean {
  return (
    message.role === "assistant" &&
    answerOf(message.text).kind === "silence" &&
    !message.files?.length &&
    !message.apps?.length &&
    !("connect" in message && message.connect) &&
    message.question === undefined
  );
}

/** Every row the log draws, in reading order. A message sent mid-turn stands under the live row
 *  until a drain names it, then above the segment that followed the drain. Within the live row the
 *  step line is drawn over the words streaming under it, though the words came first. */
export function layout(messages: Bubble[], live: LiveTurn | null): Row[] {
  return arrange(messages, live).filter((row) => row.kind === "live" || !silent(row.message));
}

function arrange(messages: Bubble[], live: LiveTurn | null): Row[] {
  const rows: Row[] = [];
  const said = (message: Bubble) => rows.push({ kind: "said", key: "m" + rows.length, message });
  if (live === null) {
    messages.forEach(said);
    return rows;
  }
  const cut = messages.findIndex(midTurn);
  const head = cut === -1 ? messages : messages.slice(0, cut);
  const later = cut === -1 ? [] : messages.slice(cut);
  const drained = drainedAt(later, live);
  head.forEach(said);
  closedSegments(live).forEach((segment, index) => {
    segment.forEach(said);
    later.forEach((message, at) => {
      if (drained[at] === index) said(taken(message));
    });
  });
  lastSegment(live).replies.forEach(said);
  rows.push(liveRow(live, "m" + rows.length));
  later.forEach((message, at) => {
    if (drained[at] === -1) said(message);
  });
  return rows;
}

/** The messages once the live turn has ended: its rows where the layout drew them, its answer where
 *  the live row stood, and no message waiting on a turn that is over. */
export function land(messages: Bubble[], record: LiveTurn): Bubble[] {
  const { answer } = lastSegment(record);
  return arrange(messages, record).flatMap((row) =>
    row.kind === "said" ? [taken(row.message)] : answer ? [answer] : [],
  );
}
