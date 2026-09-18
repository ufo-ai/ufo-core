import type { ComponentProps, ReactNode } from "react";

import { Card } from "@/components/ui/card";
import { cn } from "@/lib/cn";

export function Empty({ className, children, ...props }: ComponentProps<"div">) {
  return (
    <Card data-slot="empty" rows className={className} {...props}>
      <div className="m-sm flex flex-col items-center justify-center gap-2xl rounded-card border border-dashed border-edge px-2xl py-6xl text-center">
        {children}
      </div>
    </Card>
  );
}

export function EmptyHeader({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="empty-header"
      className={cn("flex max-w-hint flex-col items-center gap-sm", className)}
      {...props}
    />
  );
}

export function EmptyMedia({ children }: { children: ReactNode }) {
  return (
    <span
      data-slot="empty-media"
      className="flex items-center justify-center text-ink-soft [&_svg]:size-(--size-glyph)"
    >
      {children}
    </span>
  );
}

export function EmptyTitle({ className, ...props }: ComponentProps<"p">) {
  return (
    <p data-slot="empty-title" className={cn("m-0 text-body text-ink-soft", className)} {...props} />
  );
}

export function EmptyDescription({ className, ...props }: ComponentProps<"p">) {
  return (
    <p
      data-slot="empty-description"
      className={cn("m-0 text-label text-ink-quiet", className)}
      {...props}
    />
  );
}

export function EmptyContent({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="empty-content"
      className={cn("flex flex-wrap items-center justify-center gap-sm", className)}
      {...props}
    />
  );
}
