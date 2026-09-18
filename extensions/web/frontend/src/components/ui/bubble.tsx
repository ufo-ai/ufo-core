import type { ComponentProps } from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/cn";

const bubbleVariants = cva(
  cn(
    "group/bubble relative flex w-fit max-w-said min-w-0 flex-col gap-2xs",
    "group-data-[align=end]/message:self-end data-[align=end]:self-end",
  ),
  {
    variants: {
      variant: {
        default: "",
        said: "*:data-[slot=bubble-content]:whitespace-pre-wrap",
        ghost: cn(
          "w-full",
          "*:data-[slot=bubble-content]:rounded-none",
          "*:data-[slot=bubble-content]:border-0",
          "*:data-[slot=bubble-content]:bg-transparent",
          "*:data-[slot=bubble-content]:px-0",
          "*:data-[slot=bubble-content]:py-0",
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
  tail = false,
  className,
  ...props
}: ComponentProps<"div"> &
  VariantProps<typeof bubbleVariants> & { align?: "start" | "end"; tail?: boolean }) {
  return (
    <div
      data-slot="bubble"
      data-variant={variant}
      data-align={align}
      data-tail={tail || undefined}
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
        "border-[length:var(--size-hairline)] border-edge bg-fill",
        "group-data-[tail]/bubble:group-data-[align=start]/bubble:rounded-bl-tail",
        "group-data-[tail]/bubble:group-data-[align=end]/bubble:rounded-br-tail",
        "px-2xl py-md text-body max-narrow:text-subtitle leading-reading wrap-anywhere",
        "group-data-[align=end]/bubble:self-end",
        "[&_a]:text-link",
        className,
      )}
      {...props}
    />
  );
}
