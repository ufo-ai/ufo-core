import { beforeEach, expect, test, vi } from "vitest";

import {
  RESEARCHING,
  RESUMED_NOTE,
  answerOf,
  decodeFrame,
  fold,
  land,
  layout,
  liveTurn,
  lost,
  settled,
  type Bubble,
  type LiveTurn,
  type Row,
  type TurnFrame,
} from "@/lib/turnRecord";
import type { TerminalFrame } from "@/lib/contract";

import { ARRIVAL_ID, TURN_ID } from "./harness";

const AT = "2026-09-15T12:00:00.000Z";
const REPLY_ID = "66666666-6666-4666-8666-666666666666";
const RUN_ID = "99999999-9999-4999-8999-999999999999";
const RUN_CONVERSATION = "77777777-7777-4777-8777-777777777777";

const say = (text: string): TurnFrame => ({ kind: "message", text });
const step = (text: string, call_id = ""): TurnFrame => ({ kind: "activity", text, call_id });
const drain = (...arrivals: string[]): TurnFrame => ({ kind: "absorbed", arrivals });
const reply = (text: string, id = REPLY_ID): TurnFrame => ({ kind: "reply", id, text });
const done = (text: string, question: Record<string, unknown> | null = null): TurnFrame => ({
  kind: "terminal",
  at: AT,
  frame: {
    status: "done",
    text,
    error_class: null,
    model: "opus",
    tokens: 5,
    cost_micro_usd: 100,
    question: question as never,
  },
});
const ended = (status: TerminalFrame["status"], error_class: string | null = null): TurnFrame => ({
  kind: "terminal",
  at: AT,
  frame: { status, text: "", error_class, model: "", question: null },
});

function replay(frames: TurnFrame[], record: LiveTurn = liveTurn(TURN_ID, "opus")): LiveTurn {
  return frames.reduce(fold, record);
}

function drawn(rows: Row[]): string[] {
  return rows.map((row) =>
    row.kind === "live" ? "live:" + row.body : row.message.role + ":" + row.message.text,
  );
}

let faults: string[] = [];

beforeEach(() => {
  faults = [];
  vi.spyOn(console, "error").mockImplementation((...args: unknown[]) => {
    faults.push(String(args[0]));
  });
});

const ROUNDS = [
  say("Reading the changelog first."),
  step("Reading the changelog."),
  say("Checking the tags now."),
  step("Checking the release tags."),
  say("It shipped Tuesday."),
];

test("the fold keeps a turn's words and steps in the order the stream told them", () => {
  expect(replay(ROUNDS).steps).toEqual([
    { kind: "text", text: "Reading the changelog first.", open: false },
    { kind: "tool", label: "Reading the changelog.", call_id: "", sources: [], open: false },
    { kind: "text", text: "Checking the tags now.", open: false },
    { kind: "tool", label: "Checking the release tags.", call_id: "", sources: [], open: false },
    { kind: "text", text: "It shipped Tuesday.", open: true },
  ]);
});

test("a settled turn states the reply the transcript states back for the same rounds", () => {
  expect(settled(replay([...ROUNDS, done("It shipped Tuesday.")]))).toEqual([
    {
      role: "assistant",
      text: "It shipped Tuesday.",
      turn: TURN_ID,
      at: AT,
      summary: { model: "opus", tokens: 5, cost_micro_usd: 100 },
      events: [
        { kind: "note", text: "Reading the changelog first." },
        { kind: "activity", text: "Reading the changelog." },
        { kind: "note", text: "Checking the tags now." },
        { kind: "activity", text: "Checking the release tags." },
      ],
    },
  ]);
});

test("a drain cuts the turn into the wordless reply that did the work and the reply that answered", () => {
  const record = replay([
    say("Thinking about ducks."),
    step("Counting ducks."),
    drain(ARRIVAL_ID),
    say("2"),
    done("2"),
  ]);
  expect(settled(record)).toEqual([
    {
      role: "assistant",
      text: "",
      events: [
        { kind: "note", text: "Thinking about ducks." },
        { kind: "activity", text: "Counting ducks." },
      ],
    },
    {
      role: "assistant",
      text: "2",
      turn: TURN_ID,
      at: AT,
      summary: { model: "opus", tokens: 5, cost_micro_usd: 100 },
    },
  ]);
});

test("a drain names an arrival once, and a replay of it adds no second cut", () => {
  const record = replay([drain(ARRIVAL_ID), say("2"), drain(ARRIVAL_ID)]);
  expect(record.steps.filter((held) => held.kind === "drain")).toHaveLength(1);
});

test("a message the turn drained stands above the words that followed, one still waiting stands below the live row", () => {
  const messages: Bubble[] = [
    { role: "user", text: "write a poem" },
    { role: "user", text: "1+1=", arrival_id: ARRIVAL_ID },
    { role: "user", text: "and again", queued: true },
  ];
  const live = replay([reply("Ducks glide at dusk."), drain(ARRIVAL_ID), say("2")]);
  const rows = layout(messages, live);
  expect(drawn(rows)).toEqual([
    "user:write a poem",
    "assistant:Ducks glide at dusk.",
    "user:1+1=",
    "live:2",
    "user:and again",
  ]);
  const taken = rows[2] as Extract<Row, { kind: "said" }>;
  expect(taken.message.arrival_id).toBeUndefined();
  const waiting = rows[4] as Extract<Row, { kind: "said" }>;
  expect(waiting.message.queued).toBe(true);
  expect(rows.map((row) => row.key)).toEqual(["m0", "m1", "m2", "m3", "m4"]);
});

test("a send whose response has not landed claims the drain that named its row", () => {
  const messages: Bubble[] = [
    { role: "user", text: "go" },
    { role: "user", text: "and again", queued: true, sending: "1" },
  ];
  const rows = layout(messages, replay([drain(ARRIVAL_ID)]));
  expect(drawn(rows)).toEqual(["user:go", "user:and again", "live:"]);
});

test("the live row draws the segment's last step over its words, and names what it folds", () => {
  const first = replay([
    say("First."),
    step("Reading."),
    { kind: "sources", items: [{ kind: "web", title: "Docs", url: "https://x/y", ref: "", provider: "" }] },
    say("Second."),
  ]);
  const [row] = layout([], first);
  if (row.kind !== "live") throw new Error("no live row");
  expect(row.working).toBe("Reading.");
  expect(row.body).toBe("First.\n\nSecond.");
  expect(row.sources).toEqual([]);
  expect(row.folded).toEqual([]);

  const [later] = layout([], fold(first, step("Listing.")));
  if (later.kind !== "live") throw new Error("no live row");
  expect(later.working).toBe("Listing.");
  expect(later.folded).toEqual([
    {
      kind: "tool",
      label: "Reading.",
      call_id: "",
      sources: [{ kind: "web", title: "Docs", url: "https://x/y", ref: "", provider: "" }],
      open: false,
    },
  ]);
});

test("sources with no step of their own open an unlabelled step whose tiles stand until words arrive", () => {
  const page = { kind: "web" as const, title: "Docs", url: "https://x/y", ref: "", provider: "" };
  const opened = replay([{ kind: "sources", items: [page, page] }]);
  expect(opened.steps).toEqual([{ kind: "tool", label: null, call_id: "", sources: [page], open: true }]);
  const [row] = layout([], opened);
  if (row.kind !== "live") throw new Error("no live row");
  expect(row.working).toBe(RESEARCHING);
  expect(row.sources).toEqual([page]);
  const [spoke] = layout([], fold(opened, say("Here.")));
  if (spoke.kind !== "live") throw new Error("no live row");
  expect(spoke.sources).toEqual([]);
  expect(spoke.working).toBeNull();
});

test("the opening line stands until the turn does something, and a resume states itself over the words", () => {
  const [opening] = layout([], liveTurn(TURN_ID));
  if (opening.kind !== "live") throw new Error("no live row");
  expect(opening.working).toBe(RESEARCHING);
  const [resumed] = layout([], replay([say("Half"), { kind: "resumed", attempt: "a" }, say("way")]));
  if (resumed.kind !== "live") throw new Error("no live row");
  expect(resumed.working).toBe(RESUMED_NOTE);
  expect(resumed.body).toBe("Half\n\nway");
});

test("a resume settles as the note the transcript keeps", () => {
  const record = replay([say("Half"), { kind: "resumed", attempt: "a" }, say("way"), done("way")]);
  expect(settled(record)[0].events).toEqual([
    { kind: "note", text: "Half" },
    { kind: "note", text: RESUMED_NOTE },
  ]);
});

test("a replayed reply states itself once, and a reply beside streaming words leaves them one passage", () => {
  const record = replay([say("one "), reply("Ducks."), reply("Ducks."), say("two")]);
  expect(record.steps).toEqual([
    { kind: "text", text: "one two", open: true },
    { kind: "reply", id: REPLY_ID, text: "Ducks." },
  ]);
});

test("a run's frames nest under the run they name, and one naming no held parent is a fault kept at the root", () => {
  const run = {
    turn_id: RUN_ID,
    parent_turn_id: TURN_ID,
    conversation_id: RUN_CONVERSATION,
    profile: "general_purpose",
    name: "Lookup",
    activity: "",
    status: "",
  };
  const opened = replay([{ kind: "subagent_activity", run }]);
  expect(opened.runs.map((held) => [held.turn_id, held.running])).toEqual([[RUN_ID, true]]);
  const advanced = fold(opened, { kind: "subagent_activity", run: { ...run, activity: "Looking" } });
  expect(advanced.runs[0].current).toBe("Looking");
  const closed = fold(advanced, { kind: "subagent_activity", run: { ...run, status: "done" } });
  expect(closed.runs[0].running).toBe(false);
  expect(faults).toEqual([]);

  const orphan = fold(opened, {
    kind: "subagent_activity",
    run: { ...run, turn_id: "orphan", parent_turn_id: "nobody", conversation_id: "c" },
  });
  expect(orphan.runs).toHaveLength(2);
  expect(faults).toEqual(["ufo: run.orphan"]);
});

test("a frame after the turn ended, a second end, and a wordless reply are faults that change nothing", () => {
  const over = replay([say("Done."), done("Done.")]);
  expect(fold(over, say("more"))).toBe(over);
  expect(fold(over, ended("cancelled"))).toBe(over);
  const record = liveTurn(TURN_ID);
  expect(fold(record, reply(""))).toBe(record);
  expect(faults).toEqual(["ufo: frame.after_end", "ufo: frame.after_end", "ufo: reply.wordless"]);
});

test("the boundary refuses an event the stream does not declare and a payload without its fields", () => {
  expect(decodeFrame("nonsense", "{}")).toBeNull();
  expect(decodeFrame("activity", "not json")).toBeNull();
  expect(decodeFrame("activity", "{}")).toBeNull();
  expect(decodeFrame("activity", '{"text":"Reading."}')).toEqual({
    kind: "activity",
    text: "Reading.",
    call_id: "",
  });
  expect(decodeFrame("activity", '{"text":"Reading.","call_id":"c1"}')).toEqual({
    kind: "activity",
    text: "Reading.",
    call_id: "c1",
  });
  expect(decodeFrame("terminal", '{"status":"cancelled"}', AT)).toEqual({
    kind: "terminal",
    at: AT,
    frame: { status: "cancelled" },
  });
  expect(decodeFrame("terminal", '{"status":"lost"}')).toBeNull();
  expect(faults).toEqual([
    "ufo: frame.unknown",
    "ufo: frame.malformed",
    "ufo: frame.malformed",
    "ufo: frame.malformed",
  ]);
});

test("an answer is words, the silence a turn writes when it has nothing to say, or nothing", () => {
  expect(answerOf("It shipped.")).toEqual({ kind: "words", text: "It shipped." });
  expect(answerOf("<response></response>")).toEqual({ kind: "silence" });
  expect(answerOf(" <response>\n</response> ")).toEqual({ kind: "silence" });
  expect(answerOf("<response/>")).toEqual({ kind: "silence" });
  expect(answerOf("<BR />")).toEqual({ kind: "silence" });
  expect(answerOf("<response></response> and more")).toEqual({
    kind: "words",
    text: "<response></response> and more",
  });
  expect(answerOf("  ")).toEqual({ kind: "none" });
});

test("a silent reply is no row, and one that shared a file keeps its row", () => {
  const silent: Bubble = { role: "assistant", text: "<response></response>", summary: { tokens: 800 } };
  expect(layout([silent], null)).toEqual([]);
  const shared: Bubble = {
    ...silent,
    files: [{ filename: "a.pdf", url: null, preview_url: null, media_type: "application/pdf" }],
  };
  expect(layout([shared], null)).toHaveLength(1);
});

test("landing puts the answer where the live row stood and clears every wait", () => {
  const messages: Bubble[] = [
    { role: "user", text: "write a poem" },
    { role: "user", text: "1+1=", arrival_id: ARRIVAL_ID },
    { role: "user", text: "and again", queued: true },
  ];
  const record = replay([reply("Ducks glide at dusk."), drain(ARRIVAL_ID), say("2"), done("2")]);
  const landed = land(messages, record);
  expect(landed.map((message) => message.text)).toEqual([
    "write a poem",
    "Ducks glide at dusk.",
    "1+1=",
    "2",
    "and again",
  ]);
  expect(landed.every((message) => message.arrival_id === undefined && !message.queued)).toBe(true);
  expect(landed[3].summary).toEqual({ model: "opus", tokens: 5, cost_micro_usd: 100 });
});

test("a turn that ended without a verdict keeps the words it streamed and prices nothing", () => {
  expect(settled(lost(replay([say("partial")])))).toEqual([
    { role: "assistant", text: "partial", turn: TURN_ID },
  ]);
});

test("a turn that did not finish closes its words with the line its end carries", () => {
  expect(settled(replay([say("Half"), ended("cancelled")]))[0].text).toBe("Half\nStopped.");
  expect(settled(replay([ended("failed", "Boom")]))[0].text).toBe("(failed: Boom)");
  expect(settled(replay([say("Half"), { kind: "parked", message: "Paused." }]))[0].text).toBe(
    "Half\nPaused.",
  );
});

test("a done turn on the auto model names no model, and its question rides its reply", () => {
  const record = replay(
    [done("Asked.", { title: "One", questions: [{ question: "Which?" }] })],
    liveTurn(TURN_ID, "auto"),
  );
  const [message] = settled(record);
  expect(message.summary).toEqual({ tokens: 5, cost_micro_usd: 100 });
  expect(message.question).toEqual({
    turn_id: TURN_ID,
    title: "One",
    questions: [{ question: "Which?" }],
  });
});

test("a labelled step names the call its label is for, so a surface can bind the label to the call's row", () => {
  const bound = replay([step("Reading the changelog.", "c1"), step("Listing.")]);
  expect(bound.steps.map((held) => (held.kind === "tool" ? held.call_id : null))).toEqual(["c1", ""]);
});
