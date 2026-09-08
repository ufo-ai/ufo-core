import { IconArrowDownRight, IconArrowUpRight, type TablerIcon } from "@tabler/icons-react";
import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

/** One measure drawn as a tile: what is counted, the figure, the move it made, and the rule it was
 *  counted by. The tile owns the rhythm between those parts, so a row of measures aligns label to
 *  label and figure to figure whatever each one carries — a mark before the label, a state at the
 *  far end of it, both, or neither.
 *
 *  The tile states the muted tone once and its words inherit it, which is what lets `unarmed` reach
 *  all of them in one class: a measure nothing is counting yet states what it would count in the
 *  palette's third tone, the tone a placeholder is drawn in. It recedes by tone and never by
 *  opacity — ink held back over the pane lands on a different grey in each scheme, and it takes the
 *  mark and the state beside the label down with it, so the member reads a tile that has been faded
 *  rather than one that says it is not on. */
export function Stat({
  unarmed,
  className,
  ...props
}: ComponentProps<"div"> & { unarmed?: boolean }) {
  return (
    <div
      data-slot="stat"
      data-unarmed={unarmed ? "" : undefined}
      className={cn(
        "group/stat flex min-w-0 flex-col items-start gap-2xl",
        "text-ink-soft data-unarmed:text-ink-faint",
        className,
      )}
      {...props}
    />
  );
}

/** The line over the figure: the mark and the label at the leading edge, what the caller drops at
 *  the trailing one — a badge naming the period, the state the measure is in. The line is one glyph
 *  deep and stays one glyph deep, so a row of tiles puts every figure at one height however long a
 *  label runs and whether or not a tile carries a mark. */
export function StatHeader({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="stat-header"
      className={cn("flex h-(--size-glyph) w-full items-center gap-sm", className)}
      {...props}
    />
  );
}

/** A mark on the header line — the glyph of the connector a measure was counted from, an app's
 *  avatar. It holds the glyph square and sizes what it is given to fill it, so no page spells that
 *  override itself: a mark drawn at its own size is what makes one tile in a row a line taller than
 *  the rest.
 *
 *  Which end it sits at is the caller's, because the label between them takes the width: a mark
 *  before the label reads as what the measure is about, and a mark after it as where the number came
 *  from. A mark carries no words, so a caller naming a source with one gives the slot the name. */
export function StatMedia({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      data-slot="stat-media"
      className={cn(
        "flex size-(--size-glyph) shrink-0 items-center justify-center *:size-full",
        className,
      )}
      {...props}
    />
  );
}

/** What is counted, cut at the line's width rather than wrapped: the line carries the state at its
 *  far end, so a long name is cut instead of pushing that state off the tile.
 *
 *  It is found, never read, so it takes the quiet step — a label as dark as the rule under the
 *  figure makes three greys compete for one tile. An unarmed tile's fainter tone still outranks it,
 *  because the tile states that tone on every part rather than leaving it to inheritance. */
export function StatLabel({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      data-slot="stat-label"
      className={cn("min-w-0 flex-1 truncate text-label font-medium text-ink-quiet", className)}
      {...props}
    />
  );
}

/** The figure, and beside it the move it made: one line, so the number and its change are read as
 *  one statement rather than as two. The figure takes the one step above the scale's chrome, because
 *  a tile is scanned for its number and every other word on it qualifies that number, and it takes
 *  the weight the label beside it takes: a figure set at the body weight is the one thing on the
 *  tile drawn lighter than the words that qualify it.
 *
 *  The two sit on one baseline. Bottom-aligning boxes of two sizes drops the smaller one below the
 *  figure's baseline by the mono face's descender, which reads as a delta that has slipped. */
export function StatValue({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="stat-value"
      className={cn(
        "flex w-full items-baseline gap-sm text-figure font-medium text-ink",
        "group-data-unarmed/stat:text-ink-faint",
        className,
      )}
      {...props}
    />
  );
}

export type StatTone = "default" | "up" | "down";

const DELTA_TONES: Record<StatTone, string> = {
  default: "text-ink-soft",
  up: "text-link",
  down: "text-attention-ink",
};

const DELTA_GLYPHS: Record<StatTone, TablerIcon | null> = {
  default: null,
  up: IconArrowUpRight,
  down: IconArrowDownRight,
};

/** How the measure moved, drawn beside the figure it moved. The tone states the move's **direction**
 *  and never a judgement of it: `up` is the first accent, `down` the second, and `default` the muted
 *  step for a move that held. A tone chosen for whether the news is good is a tone every page has to
 *  decide for itself, and it reads as an alarm on the measures where up is the point — shipped rose,
 *  revenue rose. Direction is a fact about the number, so the page states the fact and the member
 *  reads what it means.
 *
 *  Both accents are named by the theme, so no page spells either colour itself.
 *
 *  Direction is stated twice, as the tone and as an arrow before the move: up-right for `up`,
 *  down-right for `down`, nothing for a move that held. The accents are a blue and an orange, which
 *  is the pair a member who cannot separate them reads as one colour — so colour is the channel for
 *  the member who has it and never the only one. The arrow says what the tone says and carries no
 *  words of its own, so it is hidden from a reader being read to: the caller's text is the whole
 *  statement there. It takes the glyph square rather than the move's own em, because an outline
 *  arrow inks half the box it is given and the square is what brings its stroke to the height of the
 *  digits beside it; it is centred on the move's line while the text keeps the baseline the figure
 *  is set on.
 *
 *  The size is stated here rather than inherited: the delta sits inside the figure's line, and a
 *  move set at the figure's size reads as more digits of the figure. It takes the step under the
 *  chrome, so a tile is read as its figure, then what is counted, then how it moved. */
export function StatDelta({
  tone = "default",
  className,
  children,
  ...props
}: ComponentProps<"span"> & { tone?: StatTone }) {
  const Glyph = DELTA_GLYPHS[tone];
  return (
    <span
      data-slot="stat-delta"
      className={cn(
        "inline-flex items-baseline gap-2xs text-small font-medium",
        DELTA_TONES[tone],
        "group-data-unarmed/stat:text-ink-faint",
        className,
      )}
      {...props}
    >
      {Glyph ? <Glyph aria-hidden className="size-(--size-glyph) shrink-0 self-center" /> : null}
      {children}
    </span>
  );
}

/** The rule the figure was counted by, wrapped and given the whole width: a number whose rule
 *  nobody can state is a number nobody can act on, so the sentence that states it is part of the
 *  tile rather than a tooltip the member has to find. */
export function StatDescription({ className, ...props }: ComponentProps<"p">) {
  return (
    <p data-slot="stat-description" className={cn("m-0 w-full text-small", className)} {...props} />
  );
}
