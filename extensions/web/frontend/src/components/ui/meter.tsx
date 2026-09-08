import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

export type MeterTone = "primary" | "secondary";

export type MeterPart = {
  label: string;
  value: number;
  tone: MeterTone;
};

const TONES: Record<MeterTone, string> = {
  primary: "bg-live",
  secondary: "bg-blocked",
};

/** How a whole divides, drawn as one bar: each share takes the width it is worth, in the order it
 *  is given, and whatever is unaccounted for stays as the track behind them. A bar is the shape for
 *  a division whose parts sum to something a member already knows — spend against a cap, a period
 *  against its budget — because the eye compares two lengths on one line without reading either
 *  figure.
 *
 *  It is one bar and no tile. The figure over it, the move it made and what it counts are a `Stat`,
 *  which owns that line and that rhythm already; the bar fills the width it is given and shrinks for
 *  whatever stands at the end of its row, so a stack of faces sits beside it without either
 *  component knowing about the other. A second tile here would draw the same three parts twice.
 *
 *  A share the caller gives as zero draws nothing rather than a sliver, so a part that has not
 *  started is absent from the bar instead of being a mark the reader has to discount. Parts summing
 *  past the whole are held at it: a bar that ran past its own end would state a proportion no reader
 *  could take off it.
 *
 *  The two tones are the palette's two accents, which is what the portal already marks live and
 *  blocked work with. They name a part, never a verdict on it — a bar of two colours is two shares,
 *  and which of them is the good news is the page's to say in words.
 *
 *  A share is announced as its share of the whole, because that is the one thing the bar says that
 *  holds in any unit. The values a caller passes are whatever it counts in — cents, seconds, runs —
 *  and reading them out states a figure that appears nowhere on the screen: a bar drawn from 2680
 *  and 460 sits under the words "$31.40 of 10000". A proportion is what a sighted reader takes off
 *  two lengths on one line, so it is what a reader by ear is given. */
export function Meter({
  label,
  parts,
  of,
  className,
  ...props
}: ComponentProps<"div"> & { label: string; parts: readonly MeterPart[]; of: number }) {
  const whole = of > 0 ? of : 1;
  const drawn = parts.filter((part) => part.value > 0);
  const counted = drawn.reduce((sum, part) => sum + part.value, 0);
  const held = counted > whole ? whole / counted : 1;
  return (
    <div
      data-slot="meter"
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={whole}
      aria-valuenow={Math.min(counted, whole)}
      aria-valuetext={drawn
        .map((part) => `${part.label} ${Math.round((part.value * held * 100) / whole)}%`)
        .join(", ")}
      className={cn(
        "flex h-2xs w-full min-w-0 items-stretch overflow-hidden rounded-row bg-fill",
        className,
      )}
      {...props}
    >
      {drawn.map((part, cell) => (
        <span
          key={`${cell}:${part.label}`}
          aria-hidden
          data-slot="meter-part"
          data-tone={part.tone}
          className={TONES[part.tone]}
          style={{ width: `${((part.value * held) / whole) * 100}%` }}
        />
      ))}
    </div>
  );
}
