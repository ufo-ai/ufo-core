import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/cn";

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

export function Item({ className, variant, size, ...props }: ItemProps) {
  return (
    <li data-slot="item" className={cn(itemVariants({ variant, size }), className)} {...props} />
  );
}

export function ItemSeparator() {
  return <li aria-hidden className="border-t border-edge" />;
}

export function ItemMedia({ children }: { children: ReactNode }) {
  return (
    <div data-slot="item-media" className="flex shrink-0 items-center">
      {children}
    </div>
  );
}

export function MarkTile({ children }: { children: ReactNode }) {
  return (
    <span
      data-slot="mark"
      className="flex size-(--size-touch) shrink-0 items-center justify-center rounded-control bg-fill"
    >
      {children}
    </span>
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

export function ItemActions({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("flex shrink-0 items-center gap-sm", className)}>{children}</div>;
}
