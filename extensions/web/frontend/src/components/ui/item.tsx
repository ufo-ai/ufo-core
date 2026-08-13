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

export function Item({ className, ...props }: ComponentProps<"li">) {
  return <li data-slot="item" className={cn("flex items-center gap-lg px-xl py-lg", className)} {...props} />;
}

/** The rule between two items, drawn as its own row so no item carries an edge of its own and the
 *  last one meets the card with nothing beside it. */
export function ItemSeparator() {
  return <li aria-hidden className="border-t border-edge-soft" />;
}

export function ItemContent({ children }: { children: ReactNode }) {
  return <div className="flex min-w-0 flex-1 flex-col gap-2xs">{children}</div>;
}

export function ItemTitle({ children }: { children: ReactNode }) {
  return (
    <div data-slot="item-title" data-part="primary" className="truncate font-display text-body">
      {children}
    </div>
  );
}

export function ItemDescription({ children }: { children: ReactNode }) {
  return (
    <p data-part="body" className="m-0 truncate text-small opacity-(--opacity-muted-soft)">
      {children}
    </p>
  );
}

export function ItemActions({ children }: { children: ReactNode }) {
  return <div className="flex shrink-0 items-center gap-sm">{children}</div>;
}
