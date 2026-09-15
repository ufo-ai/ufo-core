import * as HoverCardPrimitive from "@radix-ui/react-hover-card";
import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

/** A card of what stands behind a row — read where the row is, without opening it. */
export function HoverCard({ openDelay = 300, ...props }: ComponentProps<typeof HoverCardPrimitive.Root>) {
  return <HoverCardPrimitive.Root openDelay={openDelay} {...props} />;
}

/** The row the card belongs to; `asChild` makes the child the trigger. The pointer and the
 *  keyboard both open it, so a row reached by tab states as much as a row under the pointer. */
export const HoverCardTrigger = HoverCardPrimitive.Trigger;

export function HoverCardContent({ className, side = "right", sideOffset = 8, ...props }: ComponentProps<typeof HoverCardPrimitive.Content>) {
  return (
    <HoverCardPrimitive.Portal>
      <HoverCardPrimitive.Content
        data-slot="hover-card-content"
        side={side}
        sideOffset={sideOffset}
        className={cn(
          "z-50 flex w-(--container-connect) flex-col gap-2xs rounded-menu border border-edge bg-popover p-sm px-(--spacing-md) text-ui text-popover-foreground [box-shadow:var(--shadow-raised)] animate-raise",
          className,
        )}
        {...props}
      />
    </HoverCardPrimitive.Portal>
  );
}
