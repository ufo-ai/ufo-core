import { useState } from "react";

import { cva } from "class-variance-authority";
import {
  IconAlertTriangle,
  IconChevronDown,
  IconClockPause,
  IconDotsCircleHorizontal,
  IconPlayerStopFilled,
  IconPlugX,
  IconPointFilled,
  type TablerIcon,
} from "@tabler/icons-react";

import { DecodeLine } from "@/components/ui/decode";

import { Sources } from "@/components/ui/sources";
import { agentName } from "@/lib/agentName";
import { cn } from "@/lib/cn";
import type { StepLine } from "@/lib/turnRecord";
import type { SourceRef, SubagentRun } from "@/lib/types";

const AGENT_PROFILE = "agent:";

function runName(run: SubagentRun): string {
  if (run.name) return run.name;
  return run.profile.startsWith(AGENT_PROFILE)
    ? "App · " + agentName(run.profile.slice(AGENT_PROFILE.length))
    : "Subagent · " + run.profile;
}

/** What a run has done, in order, with the step it is on last where the record has not caught up. */
function runSteps(run: SubagentRun): string[] {
  const done = run.events.map((event) => event.text).filter(Boolean);
  return run.current && done.at(-1) !== run.current ? [...done, run.current] : done;
}

type Cell = "line" | "blank" | "tee" | "end";

type Branch = { id: string; stem: Cell[]; step: string; sources: SourceRef[] };

type Node = { id: string; label: string; sources: SourceRef[]; kids: Node[] };

function nodesOf(runs: SubagentRun[]): Node[] {
  return runs.map((run) => {
    const id = run.turn_id ?? run.conversation_id;
    return {
      id,
      label: runName(run),
      sources: [],
      kids: [
        ...runSteps(run).map((step, at) => ({
          id: id + ":" + at,
          label: step,
          sources: [],
          kids: [],
        })),
        ...nodesOf(run.subagents),
      ],
    };
  });
}

/** One line per node. Each carries one cell per level: the ancestors it hangs under, then `├─`
 *  while a sibling follows or `└─` where it is the last. */
function lines(nodes: Node[], stem: Cell[] = []): Branch[] {
  return nodes.flatMap((node, index) => {
    const last = index === nodes.length - 1;
    return [
      { id: node.id, stem: [...stem, last ? "end" : "tee"], step: node.label, sources: node.sources },
      ...lines(node.kids, [...stem, last ? "blank" : "line"]),
    ];
  });
}

/** How a turn's record ends, as `TurnEnd` in `src/lib/contract.ts` declares it: a terminal frame's
 *  `cancelled`, `failed` or round-budget `incomplete`, a parked end, or a lost stream. */
export type TurnEnding =
  | { kind: "cancelled" }
  | { kind: "failed" }
  | { kind: "incomplete" }
  | { kind: "parked"; message: string }
  | { kind: "lost" };

const VERDICT = cva("flex min-w-0 items-start gap-xs", {
  variants: {
    ending: {
      cancelled: "text-ink-soft",
      failed: "text-attention-ink",
      incomplete: "text-ink-soft",
      parked: "text-attention-ink",
      lost: "text-ink-soft",
    },
  },
});

function verdict(ended: TurnEnding): { mark: TablerIcon; words: string } {
  switch (ended.kind) {
    case "cancelled":
      return { mark: IconPlayerStopFilled, words: "Stopped." };
    case "failed":
      return {
        mark: IconAlertTriangle,
        words: "The turn stopped before it finished. Send the message again.",
      };
    case "incomplete":
      return {
        mark: IconDotsCircleHorizontal,
        words: "The turn hit its step limit and answered with what it had. Ask for the rest.",
      };
    case "parked":
      return { mark: IconClockPause, words: ended.message };
    case "lost":
      return {
        mark: IconPlugX,
        words: "The connection dropped. The turn is still running; reload to read it.",
      };
  }
}

/** The terminal frame carries `error_class`, a Python exception name a member can do nothing with;
 *  these words state what is true and what to do next, and the mark tells them from the answer. */
function Verdict({ ended }: { ended: TurnEnding }) {
  const { mark: Mark, words } = verdict(ended);
  return (
    <span className={VERDICT({ ending: ended.kind })}>
      <Mark aria-hidden className="size-(--size-glyph) shrink-0" />
      <span className="min-w-0">{words}</span>
    </span>
  );
}

const RULE = "absolute bg-edge";

/** One level of the tree, drawn as rules rather than characters: a proportional face has no cell
 *  width to line box-drawing glyphs up in. */
function Stem({ cell }: { cell: Cell }) {
  return (
    <span aria-hidden className="relative w-2xl shrink-0 self-stretch">
      {cell === "blank" ? null : (
        <span className={cn(RULE, "left-1/2 w-px", cell === "end" ? "top-0 h-1/2" : "inset-y-0")} />
      )}
      {cell === "tee" || cell === "end" ? (
        <span className={cn(RULE, "top-1/2 left-1/2 h-px w-1/2")} />
      ) : null}
    </span>
  );
}

/** The step a turn is on, what it has read, and the tree of subagents under it: a count alone reads
 *  as a stuck turn. Once the turn settles the tree folds away behind how long the reasoning took,
 *  which is the one thing a member reads it back for — or behind the verdict, where the turn ended
 *  in anything but a plain answer. */
export function TurnActivity({
  working,
  runs,
  steps = [],
  reading = [],
  took,
  settled = false,
  ended,
}: {
  working: string | null;
  runs: SubagentRun[];
  /** Every step the turn has closed, each with what it read. */
  steps?: StepLine[];
  /** What the step a turn is on has read so far, drawn beside its words. */
  reading?: SourceRef[];
  /** How long the turn took, as the summary states it. A transcript read back carries it; a turn
   *  with none says only that its reasoning is done. */
  took?: number;
  settled?: boolean;
  /** How the turn ended, where it ended in anything but a plain answer. It stands where the
   *  duration would, so the verdict is the line the member reads and the steps fold under it. */
  ended?: TurnEnding;
}) {
  const [open, setOpen] = useState(false);

  const tree = lines([
    ...steps.map((step, at) => ({ id: "step:" + at, ...step, kids: [] })),
    ...nodesOf(runs),
  ]);
  const over = settled || ended !== undefined;
  /* Only a settled turn folds: one step states itself and earns no caret. A verdict states itself
     whatever it holds, so every step it ran folds under it. */
  const folds = ended !== undefined ? tree.length > 0 : tree.length > 1 || runs.length > 0;
  if (ended === undefined && tree.length === 0 && (settled || !working)) return null;
  const step =
    ended !== undefined
      ? null
      : settled
        ? folds
          ? took === undefined
            ? "Completed reasoning"
            : "Completed reasoning in " + Math.max(1, Math.round(took / 1000)) + "s"
          : tree[0].step
        : working;

  const head =
    ended !== undefined ? (
      <Verdict ended={ended} />
    ) : (
      <span className={cn("min-w-0 truncate", !settled && "shimmer")}>
        {settled ? step : <DecodeLine text={step ?? ""} />}
      </span>
    );
  const read = reading.length ? (
    <span className="flex shrink-0 items-center ps-sm">
      <Sources sources={reading} />
    </span>
  ) : null;

  return (
    <div className="flex w-full min-w-0 flex-col gap-2xs text-ui text-ink-soft">
      {ended !== undefined || step ? (
        over && folds ? (
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen((held) => !held)}
            className={cn(
              "flex min-w-0 max-w-full items-center gap-xs border-0 bg-transparent p-0",
              "cursor-pointer text-start text-inherit hover:text-ink",
              ended !== undefined && "items-start",
            )}
          >
            {head}
            {read}
            <IconChevronDown
              aria-hidden
              className={cn(
                "size-(--size-glyph) shrink-0 transition-transform duration-100 ease-control",
                "motion-reduce:transition-none",
                open && "rotate-180",
              )}
            />
          </button>
        ) : (
          <div
            className={cn(
              "flex min-w-0 max-w-full items-center gap-xs",
              ended !== undefined && "items-start",
            )}
          >
            {head}
            {read}
          </div>
        )
      ) : null}
      {tree.length > 0 && (!over || (folds && open)) ? (
        <div className="flex flex-col">
          {tree.map((branch) => (
            <div key={branch.id} className="unfolds">
              <div className="flex min-w-0 items-stretch overflow-hidden leading-chrome">
              {branch.stem.map((cell, level) => (
                <Stem key={level} cell={cell} />
              ))}
              <span className="flex shrink-0 items-center pe-xs">
                <IconPointFilled aria-hidden className="size-(--size-dot) text-ink-quiet" />
              </span>
              {/* What a step read closes its own words rather than standing at the row's far edge. */}
              <span className="min-w-0 self-center truncate">{branch.step}</span>
              {branch.sources.length ? (
                <span className="flex shrink-0 items-center ps-sm">
                  <Sources sources={branch.sources} />
                </span>
              ) : null}
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
