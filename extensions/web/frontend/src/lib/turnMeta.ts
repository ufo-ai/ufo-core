import { money } from "@/lib/money";
import type { TurnSummary } from "@/lib/types";

/** The count beside a running turn's clock, short enough to re-read every second: `800`, `29K`, `1.4M`. */
export function tokens(count: number): string {
  return count.toLocaleString("en-US", { notation: "compact", maximumFractionDigits: 1 });
}

/** The spend line under a settled reply, composed the same way for the turn a member watches and
 *  for the one they read back. The model rides the line as its own mark, so it is not spelled
 *  here, and a part the summary does not name is left out rather than drawn empty. */
export function turnMeta(summary: TurnSummary): string {
  const parts: string[] = [];
  if (summary.tokens !== undefined) parts.push(tokens(summary.tokens) + " tok");
  if (summary.cost_micro_usd !== undefined) parts.push(money(summary.cost_micro_usd));
  return parts.join(" · ");
}
