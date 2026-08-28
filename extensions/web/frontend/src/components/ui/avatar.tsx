import * as AvatarPrimitive from "@radix-ui/react-avatar";
import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

/** The circle a member's initial and an app's mark are drawn in, at the one `--size-avatar` the
 *  portal gives them both: the initial in the top bar and the mark on a picker row are the same
 *  shape on the same secondary ground, so neither drifts from the other. */
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

/** What the circle holds. The portal carries no photograph and no app image, so this is the whole
 *  of an avatar's content — a member's initials, an app's mark, a company's: with no image beside
 *  it the circle never enters a loading state, and Radix draws this the moment it mounts. */
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
