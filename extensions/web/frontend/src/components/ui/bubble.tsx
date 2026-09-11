import type { ComponentProps } from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/cn";

const bubbleVariants = cva(
  cn(
    "group/bubble relative flex w-fit max-w-said min-w-0 flex-col gap-2xs",
    "group-data-[align=end]/message:self-end data-[align=end]:self-end",
    "data-[variant=ghost]:max-w-full",
  ),
  {
    variants: {
      variant: {
        default: "*:data-[slot=bubble-content]:bg-fill",
        ghost: cn(
          "*:data-[slot=bubble-content]:rounded-none",
          "*:data-[slot=bubble-content]:bg-transparent",
          "*:data-[slot=bubble-content]:p-0",
        ),
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export function Bubble({
  variant = "default",
  align = "start",
  className,
  ...props
}: ComponentProps<"div"> & VariantProps<typeof bubbleVariants> & { align?: "start" | "end" }) {
  return (
    <div
      data-slot="bubble"
      data-variant={variant}
      data-align={align}
      className={cn(bubbleVariants({ variant }), className)}
      {...props}
    />
  );
}

export function BubbleContent({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="bubble-content"
      className={cn(
        "w-fit max-w-full min-w-0 overflow-hidden rounded-bubble",
        "p-2xl text-label max-narrow:text-subtitle leading-reading wrap-anywhere",
        "group-data-[align=end]/bubble:self-end",
        className,
      )}
      {...props}
    />
  );
}
