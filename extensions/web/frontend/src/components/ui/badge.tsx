import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

export const badgeVariants = cva(
  cn(
    "inline-flex h-4xl shrink-0 items-center justify-center",
    "rounded-row px-sm text-fine whitespace-nowrap",
  ),
  {
    variants: {
      tone: {
        default: "bg-fill text-ink-quiet",
        attention: "bg-attention text-ink",
        affirm: "bg-affirm text-link",
        connected: "bg-connected text-connected-ink gap-xs",
      },
    },
    defaultVariants: { tone: "default" },
  },
);

export type BadgeProps = ComponentProps<"span"> & VariantProps<typeof badgeVariants>;

/** The state a record is in, drawn beside the line that names it: a pill on the fill step, at the
 *  one height and corner every chip in the portal takes. It is a statement and not an act, so it
 *  takes no border and no pointer tone — a chip that takes a control's square corner reads as a
 *  button the member should press and cannot.
 *
 *  It holds its width against the text beside it: a status is the shortest thing on the row and the
 *  first thing cut when a cell squeezes, so the pill never shrinks and never wraps, and the label
 *  beside it truncates instead.
 *
 *  It is laid out as a flex line, so a caller drawing more than one child in it names the space
 *  between them as a gap: a literal space between two elements is white space at a flex item's
 *  edge, which is collapsed away, and `#2040 SSO for enterprise plans` would set as
 *  `#2040SSO for enterprise plans`.
 *
 *  `attention` is the one tone that asks for the member — a commitment past its date, a run that
 *  stopped — and it names the palette's second accent at the weight a status is tinted at, so no
 *  screen spells that colour itself and every late row across the portal is the same colour.
 *  `affirm` is the first accent at the same weight: the chip that greets rather than warns, with
 *  the word in the ink a link takes so it still clears the pane. */
export function Badge({ className, tone, ...props }: BadgeProps) {
  return (
    <span
      data-slot="badge"
      className={cn(badgeVariants({ tone }), className)}
      {...props}
    />
  );
}

/** The one pill that says a thing is wired up, drawn beside the name rather than in the row's acts.
 *  Its label names which thing, so a reader hears "Slack connected" rather than a bare state. */
export function ConnectedBadge({ label }: { label: string }) {
  return (
    <Badge tone="connected" aria-label={label + " connected"}>
      <span className="size-xs shrink-0 rounded-full bg-live" aria-hidden />
      Connected
    </Badge>
  );
}
