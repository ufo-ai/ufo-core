import type { ComponentProps, ReactElement, ReactNode } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/cn";

/** The row reaches the sidebar's own edges so the gutter beside a title answers the pointer, and
 *  the pill it paints is inset from them. A pill that was itself the hit area left two dead
 *  strips a member crossing the list from the left never got a hover out of. */
export const SIDEBAR_ROW = "group/row flex min-h-(--size-row) w-full items-center px-sm";

export const SIDEBAR_PILL =
  "flex min-h-(--size-row) w-full min-w-0 items-center rounded-row group-hover/row:bg-fill";

export const SIDEBAR_CURRENT = "bg-fill";

export const SIDEBAR_PRESS =
  "flex min-w-0 flex-1 items-center gap-md self-stretch border-0 bg-transparent px-sm " +
  "text-left text-label text-inherit";

export const SIDEBAR_FOLDED = "justify-center gap-0 px-0";

export function SidebarRow({
  current,
  className,
  children,
  ...props
}: ComponentProps<"li"> & { current?: boolean }) {
  return (
    <li {...props} className={cn(SIDEBAR_ROW, className)}>
      <div className={cn(SIDEBAR_PILL, current === true && SIDEBAR_CURRENT)}>{children}</div>
    </li>
  );
}

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
      aria-label={collapsed === true ? label : props["aria-label"]}
      className={cn(SIDEBAR_PRESS, collapsed === true && SIDEBAR_FOLDED, className)}
    >
      {glyph}
      {collapsed === true
        ? null
        : (children ?? <span className="min-w-0 flex-1 truncate">{label}</span>)}
    </button>
  );
}

export type Chord = { key: string; cap: string; aria: string };

const CAP = cn(
  "pointer-events-none flex h-4xl shrink-0 items-center justify-center",
  "rounded-key pl-xs pr-2xs font-sans text-small tracking-key",
);

/** The cap is drawn for the eye alone: the row states its chord in `aria-keyshortcuts`, and a cap left
 *  in the tree would read the chord into the row's own name. */
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
