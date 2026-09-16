import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

export function Message({
  className,
  align = "start",
  ...props
}: ComponentProps<"div"> & { align?: "start" | "end" }) {
  return (
    <div
      data-slot="message"
      data-align={align}
      className={cn(
        "group/message relative flex w-full min-w-0 gap-sm",
        "data-[align=end]:flex-row-reverse",
        className,
      )}
      {...props}
    />
  );
}

export function MessageContent({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="message-content"
      className={cn(
        "flex w-full min-w-0 flex-col gap-md wrap-anywhere",
        "group-data-[align=end]/message:*:data-slot:self-end",
        className,
      )}
      {...props}
    />
  );
}

/** The small soft line over a turn. `bubble` insets it by a bubble's own padding, so a name in it
 *  starts on the same vertical as the words beneath it. */
export function MessageHeader({
  className,
  bubble,
  ...props
}: ComponentProps<"div"> & { bubble?: boolean }) {
  return (
    <div
      data-slot="message-header"
      className={cn(
        "flex max-w-full min-w-0 items-center text-small font-medium text-ink-soft",
        bubble ? "px-2xl" : "px-lg",
        "group-has-data-[variant=ghost]/message:px-0",
        className,
      )}
      {...props}
    />
  );
}
