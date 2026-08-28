import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

export type LegendTone = "primary" | "secondary" | "muted";

const TONES: Record<LegendTone, string> = {
  primary: "bg-live",
  secondary: "bg-blocked",
  muted: "bg-ink-faint",
};

/** What the colours in a graphic stand for, as a row of named swatches under it. A bar run banded
 *  into three tones and a meter drawn in two are both graphics a member can read the shape of and
 *  not the meaning: length is self-evident, colour never is, and a page that spends a second tone
 *  owes the reader the name that goes with it.
 *
 *  It is a list, and the list IS the legend a reader by ear gets: the names come in the order the
 *  graphic bands them, so the second entry is the second tone. Nothing here labels itself a legend
 *  a second time in prose — what the graphic counts is the `Stat` or the `Section` heading over it,
 *  and a caption under a caption is a line the member did not ask for.
 *
 *  The entries wrap rather than truncate. A name is the one thing on this row nobody can infer from
 *  the graphic, so a narrow pane takes a second line instead of cutting the word that carries the
 *  whole point.
 *
 *  It draws no ground and no edge: a legend belongs to the graphic above it, and a box of its own
 *  would state a second unit on a band that already decided what its units are. */
export function Legend({ className, ...props }: ComponentProps<"ul">) {
  return (
    <ul
      data-slot="legend"
      className={cn("flex min-w-0 flex-wrap gap-2xl", className)}
      {...props}
    />
  );
}

/** One tone and the name it stands for. The tones are the three `ChartBar` takes and the two
 *  `Meter` takes, under those same names, so a caller pairs a part with its entry by name and
 *  cannot hand `primary` to the graphic and draw `secondary` beneath it. No page spells a colour:
 *  the two accents are the theme's, and `muted` is the ink step held back, which is what the banded
 *  run already draws its earlier periods in.
 *
 *  The swatch is a circle, at the size the portal marks a state with — a square at this size reads
 *  as a chip the member should press. It never shrinks, so the name gives way first, and it is
 *  hidden from a reader by ear: the name beside it is the answer, and a colour said in words is the
 *  same fact twice. */
export function LegendItem({
  tone,
  children,
  className,
  ...props
}: ComponentProps<"li"> & { tone: LegendTone }) {
  return (
    <li
      data-slot="legend-item"
      className={cn("flex min-w-0 items-center gap-2xs text-small text-ink-soft", className)}
      {...props}
    >
      <span
        aria-hidden
        data-slot="legend-swatch"
        data-tone={tone}
        className={cn("size-sm shrink-0 rounded-full", TONES[tone])}
      />
      {children}
    </li>
  );
}
