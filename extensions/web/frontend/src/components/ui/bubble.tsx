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
        said: "*:data-[slot=bubble-content]:bg-said",
        ghost: cn(
          "w-full",
          "*:data-[slot=bubble-content]:rounded-none",
          "*:data-[slot=bubble-content]:bg-transparent",
          "*:data-[slot=bubble-content]:p-0",
        ),
      },
      entering: { true: "animate-appear", false: "" },
    },
    defaultVariants: { variant: "default", entering: false },
  },
);

export function Bubble({
  variant = "default",
  entering,
  align = "start",
  className,
  ...props
}: ComponentProps<"div"> & VariantProps<typeof bubbleVariants> & { align?: "start" | "end" }) {
  return (
    <div
      data-slot="bubble"
      data-variant={variant}
      data-align={align}
      className={cn(bubbleVariants({ variant, entering }), className)}
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
        "group-data-[variant=said]/bubble:whitespace-pre-wrap",
        "[&_a]:text-link",
        className,
      )}
      {...props}
    />
  );
}
