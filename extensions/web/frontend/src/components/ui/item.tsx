import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/cn";

/** A list of records read as one card of rows divided by hairlines: the name over its one line of
 *  prose on the left, the record's state and its acts on the right. A grid of cards holds its
 *  rhythm only while every card is the same height, so a directory whose rows carry a sentence
 *  each reads as a column of items instead — one row is one record, and the eye runs down one
 *  left edge rather than across two. */
export function ItemGroup({ className, ...props }: ComponentProps<"ul">) {
  return (
    <ul
      data-slot="item-group"
      className={cn("m-0 list-none rounded-panel border border-edge bg-card text-card-foreground p-0", className)}
      {...props}
    />
  );
}

export const itemVariants = cva("flex items-center", {
  variants: {
    variant: {
      default: "",
      muted: "bg-fill",
    },
    size: {
      default: "gap-lg px-xl py-lg",
      flush: "gap-0 p-0",
    },
  },
  defaultVariants: { variant: "default", size: "default" },
});

export type ItemProps = ComponentProps<"li"> & VariantProps<typeof itemVariants>;

/** One record on one line: what it leads with at the left edge, what it is in the middle, its state
 *  and its acts at the right. The row draws no edge of its own — the card's is `ItemGroup`'s and the
 *  rules between rows are `ItemSeparator`'s — so a row added, moved or dropped can neither double a
 *  hairline nor leave one hanging, and a bordered record standing on its own is a `Card` rather than
 *  a row wearing a second edge inside the card's.
 *
 *  `muted` marks the row a member already has open beside the page. The mark is ground, on the fill
 *  step the pointer answers with, so a held row and a hovered row read as the same act at rest and
 *  under the cursor instead of as two different ones.
 *
 *  `size="flush"` gives the inset away: the row's whole area becomes the press target of the one
 *  child that carries it — a link drawn to the row's edges — rather than a target inset inside a
 *  padded row, which is what leaves a member's click on a row's own padding doing nothing. */
export function Item({ className, variant, size, ...props }: ItemProps) {
  return (
    <li data-slot="item" className={cn(itemVariants({ variant, size }), className)} {...props} />
  );
}

/** The rule between two items, drawn as its own row so no item carries an edge of its own and the
 *  last one meets the card with nothing beside it. */
export function ItemSeparator() {
  return <li aria-hidden className="border-t border-edge" />;
}

/** What a row leads with: the moment it belongs to, the member it is about, or the faces of the
 *  members it is about. The slot carries no ground and no box of its own, because everything drawn
 *  in it brings one — a badge is a pill on the fill step, an avatar is a circle — and it holds that
 *  width while the title beside it is cut.
 *
 *  It sets no gap. A stack of faces overlaps by the measure it spells, and a gap here would take
 *  that measure back off it, so the slot spaces nothing and whatever draws the faces owns the
 *  overlap — the same element then stacks them at either end of the row. */
export function ItemMedia({ children }: { children: ReactNode }) {
  return (
    <div data-slot="item-media" className="flex shrink-0 items-center">
      {children}
    </div>
  );
}

export function ItemContent({ children }: { children: ReactNode }) {
  return <div className="flex min-w-0 flex-1 flex-col gap-2xs">{children}</div>;
}

export function ItemTitle({ children }: { children: ReactNode }) {
  return (
    <div data-slot="item-title" data-part="primary" className="truncate text-body font-medium">
      {children}
    </div>
  );
}

/** The row's one line of prose. It is cut at the row's width by default, because a column of rows
 *  holds its rhythm only while every row is one line deep. A record with no screen of its own takes
 *  `whole`: the row is all there is to read it on, so its sentence runs to the end. */
export function ItemDescription({
  children,
  whole,
}: {
  children: ReactNode;
  whole?: boolean;
}) {
  return (
    <p
      data-part="body"
      className={cn("m-0 text-small text-ink-soft", whole ? "text-pretty" : "truncate")}
    >
      {children}
    </p>
  );
}

export function ItemActions({ children }: { children: ReactNode }) {
  return <div className="flex shrink-0 items-center gap-sm">{children}</div>;
}
