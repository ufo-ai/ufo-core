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

/** The picture, where the member has one. Radix holds the fallback until the bytes decode and drops
 *  back to it if they never do, so a face that fails to load reads as initials rather than a hole.
 *  The portal serves every one of these from its own origin — a picture derived from Slack or from
 *  gravatar was fetched once and stored, so no page here names a picture host. */
export function AvatarImage({ className, ...props }: ComponentProps<typeof AvatarPrimitive.Image>) {
  return (
    <AvatarPrimitive.Image
      data-slot="avatar-image"
      className={cn("size-full rounded-full object-cover", className)}
      {...props}
    />
  );
}

/** What the circle holds. An app's mark and a company's carry no picture beside them, so their
 *  circle never enters a loading state and Radix draws this the moment it mounts; a member's holds
 *  their initials until `AvatarImage` decodes, and returns to them if it never does. */
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
