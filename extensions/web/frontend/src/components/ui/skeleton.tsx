import { cn } from "@/lib/cn";

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      data-part="skeleton"
      aria-hidden
      className={cn("animate-skeleton rounded-control bg-fill-subtle", className)}
    />
  );
}
