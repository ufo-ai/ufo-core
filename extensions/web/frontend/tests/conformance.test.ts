import { expect, test } from "vitest";

import { decodeFrame, fold, liveTurn } from "@/lib/turnRecord";

import cases from "./fixtures/fold.json";
import { TURN_ID } from "./harness";

type Case = {
  name: string;
  frames: { event: string; data: string }[];
  record: { end?: { at?: string } };
};

const AT = "2026-09-15T12:00:00Z";

/** The shape both folds are compared in: no null and no absent field differ, since the reference
 *  dumps without its nulls and this fold leaves an unset optional undefined. */
function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (typeof value !== "object" || value === null) return value;
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .filter(([, held]) => held !== null && held !== undefined)
      .map(([key, held]) => [key, canonical(held)]),
  );
}

for (const held of cases as Case[]) {
  test("the portal folds the reference case: " + held.name, () => {
    const at = held.record.end?.at ?? AT;
    let record = liveTurn(TURN_ID, "opus");
    for (const row of held.frames) {
      const frame = decodeFrame(row.event, row.data, at);
      if (frame === null) throw new Error("the fixture row did not decode: " + row.event);
      if (frame.kind === "credentials") continue;
      record = fold(record, frame);
    }
    const { model: _model, files: _files, apps: _apps, connect: _connect, reconnecting: _r, ...folded } = record;
    expect(canonical(folded)).toEqual(canonical(held.record));
  });
}
