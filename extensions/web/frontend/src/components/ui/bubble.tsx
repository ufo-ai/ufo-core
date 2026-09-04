import type { ComponentProps } from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/cn";

/** What one message is drawn in. The variant is carried by the wrapper and reaches the content
 *  through it, so a bubble that holds two blocks tints both without either naming a colour.
 *
 *  There are two tones because the conversation has two sides: `default` is the member's own words
 *  on the fill step, and `ghost` is no surface at all, which is how a reply is drawn — a document
 *  in the reading column, not a card. */
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

/** The surface itself. Focus is left to the base layer's own outline: a second declaration here
 *  would answer a question the page has already answered. */
export function BubbleContent({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="bubble-content"
      className={cn(
        "w-fit max-w-full min-w-0 overflow-hidden rounded-bubble",
        "p-2xl text-label leading-reading wrap-anywhere",
        "group-data-[align=end]/bubble:self-end",
        className,
      )}
      {...props}
    />
  );
}
