import { expect, test } from "vitest";

import type { TurnRecord } from "@/lib/contract";
import { RESUMED_NOTE, layout, settled, type LiveTurn } from "@/lib/turnRecord";

import record from "./fixtures/record.json";

const held = record as TurnRecord;
const live: LiveTurn = { ...held, model: "opus", files: [], apps: [], connect: null, reconnecting: false };

test("the fixture core renders holds every step kind and ends on a terminal", () => {
  expect(new Set(held.steps.map((step) => step.kind))).toEqual(
    new Set(["text", "tool", "reply", "comment", "drain", "resumed"]),
  );
  expect(held.end?.kind).toBe("terminal");
});

test("the portal settles the record core rendered into the replies the transcript would state", () => {
  const messages = settled(live);
  expect(messages.map((message) => message.text)).toEqual([
    "Filed the launch issue as metalcraftai/ufo#1801.",
    "",
    "It shipped Tuesday.",
  ]);
  expect(messages[1].events).toEqual([
    { kind: "note", text: "Reading the changelog first." },
    { kind: "activity", text: "Reading the changelog." },
  ]);
  expect(messages[2].events).toEqual([{ kind: "note", text: RESUMED_NOTE }]);
  expect(messages[2].question?.title).toBe("One question");
  expect(messages[2].summary).toEqual({ model: "claude-opus-4-8", tokens: 12, cost_micro_usd: 110 });
  expect(messages[2].subagents?.[0].subagents[0].current).toBe("Looking up info");
});

test("the portal lays the record out with its drained segment above the live row", () => {
  const rows = layout([], live);
  expect(rows.map((row) => row.kind)).toEqual(["said", "said", "live"]);
  const last = rows[2];
  if (last.kind !== "live") throw new Error("no live row");
  expect(last.body).toBe("It shipped Tuesday.");
  expect(last.working).toBe(RESUMED_NOTE);
  expect(last.folded).toEqual([{ kind: "tool", label: null, call_id: "", sources: held.steps[1].kind === "tool" ? held.steps[1].sources : [], open: false }]);
});
