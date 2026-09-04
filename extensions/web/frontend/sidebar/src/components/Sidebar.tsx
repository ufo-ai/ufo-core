import type { ComponentProps, ReactElement, ReactNode } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
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

/** A chord a row answers: the letter the keyboard sends, the cap the row prints, and the same chord
 *  spelled for the accessibility tree. One record, so what a member reads cannot drift from what the
 *  keyboard does. */
export type Chord = { key: string; cap: string; aria: string };

/** The cap a chord is printed on: a ground and no edge, the characters opened out so a chord reads
 *  as one key rather than as a word. The tail padding is short by the tracking the last character
 *  carries, so the chord sits centred in the cap rather than pushed off its left edge. */
const CAP = cn(
  "pointer-events-none flex h-4xl shrink-0 items-center justify-center",
  "rounded-key pl-xs pr-2xs font-sans text-small tracking-key",
);

/** The chord a row answers, printed in the row's tail where the column has room for it. It is drawn
 *  only once the row is pointed at or reached by keyboard: a column of rows each carrying a chord
 *  nobody is reading is a table of keys, and the row is a place before it is a chord. It holds its
 *  box while hidden, so the name beside it keeps its measure and nothing moves under the pointer.
 *
 *  The cap is drawn for the eye alone: the row states its chord to the accessibility tree in
 *  `aria-keyshortcuts`, and a cap left in the tree would read the chord into the row's own name. */
export function SidebarCap({ chord }: { chord: Chord }) {
  return (
    <kbd
      aria-hidden
      className={cn(
        CAP,
        "bg-fill text-ink-soft opacity-0 transition-opacity duration-100 ease-control",
        "motion-reduce:transition-none",
        "group-hover/row:opacity-100 group-has-[:focus-visible]/row:opacity-100",
      )}
    >
      {chord.cap}
    </kbd>
  );
}

/** What a folded row states to the pointer: the name the fold dropped, and the chord that reaches
 *  the row without it. The cap is drawn in the tooltip's own ink held back, because the ground it
 *  stands on there is the inverse of the pane every other cap sits on. */
export function SidebarTooltip({
  collapsed,
  label,
  chord,
  children,
}: {
  collapsed: boolean;
  label: string;
  chord?: Chord;
  children: ReactElement;
}) {
  if (!collapsed) return children;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent className={cn(chord && "flex items-center gap-sm pr-sm")}>
        {label}
        {chord ? (
          <kbd aria-hidden className={cn(CAP, "bg-surface/20")}>
            {chord.cap}
          </kbd>
        ) : null}
      </TooltipContent>
    </Tooltip>
  );
}
