import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/cn";

export function ItemGroup({ className, ...props }: ComponentProps<"ul">) {
  return (
    <ul
      data-slot="item-group"
      className={cn("@container m-0 list-none p-0", className)}
      {...props}
    />
  );
}

/** A dense row's inset, carried by the row itself where the row is only read, and by the act where
 *  the row is pressed — one measure either way, so two rows in one list line up. */
const ROW_INSET = "gap-sm px-2xl py-sm";

export const itemVariants = cva(
  cn(
    "flex items-center @max-md:flex-wrap @max-md:gap-y-sm",
    "@max-md:*:data-[slot=item-actions]:w-full @max-md:*:data-[slot=item-actions]:justify-end",
  ),
  {
    variants: {
      variant: {
        default: "",
        muted: "bg-fill",
      },
      size: {
        default: "gap-2xl px-2xl py-2xl",
        row: ROW_INSET,
        flush: "gap-0 p-0",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export type ItemProps = ComponentProps<"li"> & VariantProps<typeof itemVariants>;

export function Item({ className, variant, size, ...props }: ItemProps) {
  return (
    <li data-slot="item" className={cn(itemVariants({ variant, size }), className)} {...props} />
  );
}

/** The row's own act, filling it edge to edge so the strip between two separators is one hit target
 *  and one hover. The row it fills is `flush`, so the act carries the inset. */
const ROW_ACT = cn(
  ROW_INSET,
  "flex min-w-0 flex-1 items-center text-inherit no-underline",
  "cursor-pointer border-0 bg-transparent text-start hover:bg-fill focus-visible:bg-fill",
);

export function ItemLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} className={ROW_ACT}>
      {children}
    </a>
  );
}

export function ItemPress({ onPress, children }: { onPress: () => void; children: ReactNode }) {
  return (
    <button type="button" onClick={onPress} className={ROW_ACT}>
      {children}
    </button>
  );
}

export function ItemSeparator() {
  return <li aria-hidden className="border-t border-edge" />;
}

/** The controls that narrow a card's rows, standing inside the card above them. They belong to the
 *  rows they narrow, so they hold their place while the rows under them change. */
export function ItemHead({ children }: { children: ReactNode }) {
  return (
    <li data-slot="item-head" className="flex flex-col gap-2xl px-2xl py-2xl">
      {children}
    </li>
  );
}

export function ItemMedia({ children }: { children: ReactNode }) {
  return (
    <div data-slot="item-media" className="flex shrink-0 items-center">
      {children}
    </div>
  );
}

export function MarkTile({ compact = false, children }: { compact?: boolean; children: ReactNode }) {
  return (
    <span
      data-slot="mark"
      className={cn(
        "flex shrink-0 items-center justify-center rounded-control bg-fill",
        compact ? "size-(--size-control)" : "size-(--size-touch)",
      )}
    >
      {children}
    </span>
  );
}

export function ItemContent({ children }: { children: ReactNode }) {
  return <div className="flex min-w-0 flex-1 flex-col">{children}</div>;
}

export function ItemTitle({ children }: { children: ReactNode }) {
  return (
    <div data-slot="item-title" data-part="primary" className="truncate text-ui font-medium">
      {children}
    </div>
  );
}

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
      className={cn(
        "m-0 text-small text-ink-soft",
        whole ? "text-pretty" : "truncate @max-md:overflow-visible @max-md:whitespace-normal",
      )}
    >
      {children}
    </p>
  );
}

export function ItemActions({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div data-slot="item-actions" className={cn("flex shrink-0 items-center gap-sm", className)}>
      {children}
    </div>
  );
}
