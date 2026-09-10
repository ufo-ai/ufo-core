import { money } from "@/lib/money";
import { stampMoment } from "@/lib/moments";
import type { TurnSummary } from "@/lib/types";

/** The count beside a running turn's clock, short enough to re-read every second: `800`, `29K`, `1.4M`. */
export function tokens(count: number): string {
  return count.toLocaleString("en-US", { notation: "compact", maximumFractionDigits: 1 });
}

/** The spend line under a settled reply, closed by the stamp the reply landed at, composed the same
 *  way for the turn a member watches and for the one they read back. The model rides the line as its
 *  own mark, so it is not spelled here, and a part the turn does not name is left out rather than
 *  drawn empty. The parts are returned unjoined: the line that draws them sets them apart with
 *  space rather than with a separator character. */
export function turnMeta(summary: TurnSummary, at?: string): string[] {
  const parts: string[] = [];
  if (summary.tokens !== undefined) parts.push(tokens(summary.tokens) + " tok");
  if (summary.cost_micro_usd !== undefined) parts.push(money(summary.cost_micro_usd));
  const stamp = at ? stampMoment(at) : null;
  if (stamp) parts.push(stamp);
  return parts;
}
