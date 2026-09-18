import * as AvatarPrimitive from "@radix-ui/react-avatar";
import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/cn";

/** What a mark in a stack is ringed in: a `hair` of the page's ground, so two marks that overlap
 *  read as separate objects whatever shape each is drawn in. */
const STACK_RING = "border-[length:var(--spacing-hair)] border-surface";

/** The circle a member's initial and an app's mark are drawn in, at the one `--size-avatar` the
 *  portal gives them both: the initial in the top bar and the mark on a picker row are the same
 *  shape on the same secondary ground, so neither drifts from the other.
 *
 *  `stacked` rings the circle, so faces that overlap read as separate discs. */
export function Avatar({
  className,
  stacked,
  ...props
}: ComponentProps<typeof AvatarPrimitive.Root> & { stacked?: boolean }) {
  return (
    <AvatarPrimitive.Root
      data-slot="avatar"
      className={cn(
        "flex size-(--size-avatar) shrink-0 overflow-hidden rounded-full select-none",
        stacked && STACK_RING,
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
  person = false,
  ...props
}: ComponentProps<typeof AvatarPrimitive.Fallback> & { person?: boolean }) {
  return (
    <AvatarPrimitive.Fallback
      data-slot="avatar-fallback"
      className={cn(
        "flex size-full items-center justify-center rounded-full bg-fill-strong text-small text-ink-soft",
        person && "font-medium",
        className,
      )}
      {...props}
    />
  );
}

export const AVATAR_GROUP_SHOWN = 3;

/** A run of marks read as one graphic: the first three drawn, everything past them stated as a
 *  count. They overlap by `2xs`, the step the rhythm gives faces in a stack, and the earlier one
 *  lies over the later, so the run reads left to right off the mark that leads it and every mark
 *  stays legible. The group names the whole set once, so a reader by ear hears what it stands for
 *  rather than each mark in turn. */
export function AvatarGroup({
  label,
  slot = "avatar-group",
  className,
  children,
}: {
  label: string;
  slot?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      role="img"
      aria-label={label}
      data-slot={slot}
      className={cn(
        /* Paint order stacks a later mark over an earlier one, which is the opposite of the order
           the run reads in, so each member states its own level. */
        "isolate flex items-center [&>*]:relative [&>*:not(:first-child)]:-ml-2xs",
        "[&>*:nth-child(1)]:z-40 [&>*:nth-child(2)]:z-30",
        "[&>*:nth-child(3)]:z-20 [&>*:nth-child(4)]:z-10",
        className,
      )}
    >
      {children}
    </span>
  );
}

/** The shape every mark in a group takes, so the count is the same object as the marks beside it:
 *  a face's circle squared off, at a face's size and ringed as a face is. */
export const AVATAR_GROUP_MARK = cn(
  "grid size-(--size-avatar) shrink-0 place-items-center overflow-hidden",
  "rounded-key bg-fill",
  STACK_RING,
);

/** The count closes the run it belongs to, so it takes the shape of what it is counting: the circle
 *  a face is drawn in, or the mark a source is. */
export function AvatarGroupCount({ count, round = false }: { count: number; round?: boolean }) {
  if (round) {
    return (
      <Avatar stacked>
        <AvatarFallback aria-hidden>+{count}</AvatarFallback>
      </Avatar>
    );
  }
  return (
    <span
      aria-hidden
      data-slot="avatar"
      className={cn(AVATAR_GROUP_MARK, "bg-fill-strong text-mono leading-none text-ink-soft")}
    >
      +{count}
    </span>
  );
}
