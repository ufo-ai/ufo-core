import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

export type CardTone = "default" | "attention";

const CARD_TONES: Record<CardTone, string> = {
  default: "border-edge bg-card",
  attention: "border-transparent bg-attention",
};

/** A record drawn as its own panel: a hairline box carrying a titled line, the prose that reads it,
 *  and the act a member can take on it. It is the **Frame** of the four ways to divide
 *  (`extensions/web/AGENTS.md`, "Layout: the grid, the rhythm, and the four ways to divide"): edge
 *  and ground together at `rounded-card`, drawn around a self-contained unit a member acts on — a
 *  record with its own title, prose and act, or a tile in a grid of tiles. Never around a whole
 *  band, never around a single stat, and every cell on a band is a frame or none is, since two
 *  answers to one question is what makes two screens read as two products.
 *
 *  The box owns its inset and the rhythm between its parts, so a column of cards keeps one left
 *  edge and one gap whatever each card holds, and a part dropped straight into it cannot draw
 *  against the border. The inset lives here and in none of the parts, so a card nested in another
 *  card's content sets one box inside one box instead of stacking two paddings at one edge.
 *
 *  The ground and the corner are the frame's, and no part inside it draws either. A plot, a table
 *  or a list is what the card is about, so a **Fill** nested in the frame states one shape twice
 *  and reads as a hole cut in the card instead of as its content — the same table keeps a fill off
 *  anything that already carries a border. It is the rule that lets a card stand on a filled band
 *  and read as a panel over it: one ground per box, and the box is this one.
 *
 *  `attention` tints that ground with the palette's second accent at the weight a status is tinted
 *  at — the one tone that asks for the member — and takes no hairline, because the tint is already
 *  where the card ends: a grey line laid over a warm ground is a second edge at the same boundary.
 *  The border stays, drawn in nothing, so a tinted card and a plain one measure the same. A
 *  `Badge tone="attention"` inside a tinted card says the one thing twice — tint the card or badge
 *  the row, never both. */
export function Card({
  tone = "default",
  rows = false,
  className,
  ...props
}: ComponentProps<"div"> & { tone?: CardTone; rows?: boolean }) {
  return (
    <div
      data-slot="card"
      className={cn(
        "flex min-w-0 flex-col rounded-card border p-2xl text-card-foreground",
        rows ? "gap-0 py-sm" : "gap-2xl",
        CARD_TONES[tone],
        className,
      )}
      {...props}
    />
  );
}

/** `rows` is the card as a ruled list: its children carry their own hairline, so the rule divides
 *  them and a gap must not divide them again — two dividers at one boundary is the same seam drawn
 *  twice. The vertical inset drops with the gap, because a ruled row's own padding is what holds it
 *  off the card's edge.
 *
 *  The card's head, drawn as one glyph-high band: what the card is at the leading edge, the act it
 *  offers at the trailing one. The band is one line deep and stays one line deep, so a grid of
 *  cards aligns title to title and act to act however long a title runs. The card's prose is no
 *  part of this line — it is the block beneath, which is what lets a title be cut instead of
 *  wrapping the band open. */
export function CardHeader({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="card-header"
      className={cn("flex h-(--size-glyph) w-full items-center justify-between gap-2xl", className)}
      {...props}
    />
  );
}

/** What the card is, cut at the head's width rather than wrapped: the head holds one line, so a row
 *  of cards is one height whatever their titles say. */
export function CardTitle({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="card-title"
      className={cn("min-w-0 flex-1 truncate text-label font-medium text-ink", className)}
      {...props}
    />
  );
}

/** The card's prose, and the one part of it that grows: a long sentence lengthens the card instead
 *  of pushing the act off the head's line. */
export function CardDescription({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="card-description"
      className={cn("w-full min-w-0 text-small text-ink-soft", className)}
      {...props}
    />
  );
}

/** What the card offers at the end of the head's line — a badge, a glyph, a menu. It holds that end
 *  whatever else the head carries, and keeps its width while the title beside it is cut. The band
 *  is a glyph deep, so a control drawn at its own height belongs in the footer, where the card's
 *  primary act is drawn. */
export function CardAction({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="card-action"
      className={cn("ml-auto flex shrink-0 items-center gap-sm", className)}
      {...props}
    />
  );
}

/** What the card is about, whatever that is — a table, a list of rows, a card of its own. It opens
 *  no inset, so content meets the card's padding once and a nested card reads as a box inside a box
 *  rather than as one box padded twice. */
export function CardContent({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="card-content"
      className={cn("flex min-w-0 flex-col gap-2xl", className)}
      {...props}
    />
  );
}

/** The card's foot: the act at the leading edge, what the act concerns at the trailing one. The two
 *  ends keep a gap between them, so they do not meet on a narrow card. */
export function CardFooter({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="card-footer"
      className={cn("flex w-full items-center justify-between gap-2xl", className)}
      {...props}
    />
  );
}
