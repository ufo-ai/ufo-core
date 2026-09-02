import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/cn";

/** The box every row in the sidebar is drawn in: one height, one radius, one ground under the
 *  pointer. The column holds three lists — the workspace's destinations, the apps index, the
 *  conversations rail — and a member reads down all three as one column, so the box is stated here
 *  once rather than by each list.
 *
 *  It is the row's frame and not its control, because a row may carry a second act beside the one
 *  that opens it: the ground has to run under both, or the pin at an app row's end stands outside
 *  the row it belongs to. */
export const SIDEBAR_ROW =
  "group/row flex min-h-(--size-row) w-full items-center rounded-row hover:bg-fill";

/** The fill saying which row the member is standing in. One answer for every list in the column: an
 *  app, a conversation and a workspace destination are selected the same way, so the member reads
 *  one mark rather than learning each list's own. */
export const SIDEBAR_CURRENT = "bg-fill";

/** The control filling a row. It carries no ground of its own — the frame holds the pointer and the
 *  selection — so a row with one control and a row with two are the same box. */
export const SIDEBAR_PRESS =
  "flex min-w-0 flex-1 items-center gap-md self-stretch border-0 bg-transparent px-sm " +
  "text-left text-label text-inherit";

/** The control on the glyph rail, where the fold leaves no room for words: the mark alone, centred
 *  in the row it had shared with a name. */
export const SIDEBAR_FOLDED = "justify-center gap-0 px-0";

/** One row of a sidebar list: the frame, and whatever controls the list draws in it. */
export function SidebarRow({
  current,
  className,
  ...props
}: ComponentProps<"li"> & { current?: boolean }) {
  return (
    <li {...props} className={cn(SIDEBAR_ROW, current === true && SIDEBAR_CURRENT, className)} />
  );
}

/** The act that opens what a row stands for: its mark, and the name it is read by. Folded to the
 *  glyph rail the name is dropped and stated to the pointer instead, which the list wraps this in.
 *
 *  `label` is the row's name in words. It is drawn as the row's line unless the caller draws its
 *  own — a title that travels to state its tail is still one line of one row — and it names the
 *  control for a reader who has only the mark. */
export function SidebarPress({
  current,
  collapsed,
  label,
  glyph,
  className,
  children,
  ...props
}: Omit<ComponentProps<"button">, "aria-current"> & {
  current?: boolean;
  collapsed?: boolean;
  label: string;
  glyph?: ReactNode;
}) {
  return (
    <button
      {...props}
      type="button"
      aria-current={current}
      aria-label={collapsed === true ? label : undefined}
      className={cn(SIDEBAR_PRESS, collapsed === true && SIDEBAR_FOLDED, className)}
    >
      {glyph}
      {collapsed === true
        ? null
        : (children ?? <span className="min-w-0 flex-1 truncate">{label}</span>)}
    </button>
  );
}
