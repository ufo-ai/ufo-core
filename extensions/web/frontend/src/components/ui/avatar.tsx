import * as AvatarPrimitive from "@radix-ui/react-avatar";
import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

export function Avatar({ className, ...props }: ComponentProps<typeof AvatarPrimitive.Root>) {
  return (
    <AvatarPrimitive.Root
      data-slot="avatar"
      className={cn(
        "flex size-(--size-avatar) shrink-0 overflow-hidden rounded-full select-none",
        className,
      )}
      {...props}
    />
  );
}

/** With no image beside it the circle never enters a loading state, and Radix draws this the moment it mounts. */
export function AvatarFallback({
  className,
  ...props
}: ComponentProps<typeof AvatarPrimitive.Fallback>) {
  return (
    <AvatarPrimitive.Fallback
      data-slot="avatar-fallback"
      className={cn(
        "flex size-full items-center justify-center rounded-full bg-fill-strong text-small text-ink-soft",
        className,
      )}
      {...props}
    />
  );
}
