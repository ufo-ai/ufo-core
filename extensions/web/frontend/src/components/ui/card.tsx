import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

export type CardTone = "default" | "attention";

const CARD_TONES: Record<CardTone, string> = {
  default: "border-edge bg-card",
  attention: "border-transparent bg-attention",
};

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

export function CardHeader({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="card-header"
      className={cn("flex h-(--size-glyph) w-full items-center justify-between gap-2xl", className)}
      {...props}
    />
  );
}

export function CardTitle({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="card-title"
      className={cn("min-w-0 flex-1 truncate text-label font-medium text-ink", className)}
      {...props}
    />
  );
}

export function CardDescription({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="card-description"
      className={cn("w-full min-w-0 text-small text-ink-soft", className)}
      {...props}
    />
  );
}

export function CardAction({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="card-action"
      className={cn("ml-auto flex shrink-0 items-center gap-sm", className)}
      {...props}
    />
  );
}

export function CardContent({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="card-content"
      className={cn("flex min-w-0 flex-col gap-2xl", className)}
      {...props}
    />
  );
}

export function CardFooter({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="card-footer"
      className={cn("flex w-full items-center justify-between gap-2xl", className)}
      {...props}
    />
  );
}
