import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

export const badgeVariants = cva(
  cn(
    "inline-flex h-4xl shrink-0 items-center justify-center",
    "rounded-row px-sm text-small whitespace-nowrap",
  ),
  {
    variants: {
      tone: {
        default: "bg-fill text-ink-quiet",
        attention: "bg-attention text-ink",
        affirm: "bg-affirm text-link",
      },
    },
    defaultVariants: { tone: "default" },
  },
);

export type BadgeProps = ComponentProps<"span"> & VariantProps<typeof badgeVariants>;

export function Badge({ className, tone, ...props }: BadgeProps) {
  return (
    <span
      data-slot="badge"
      className={cn(badgeVariants({ tone }), className)}
      {...props}
    />
  );
}
