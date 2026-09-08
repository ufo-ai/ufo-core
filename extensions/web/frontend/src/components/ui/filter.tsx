import { useLayoutEffect, useRef, useState } from "react";

import { cn } from "@/lib/cn";

const STEP: Record<string, number> = { ArrowRight: 1, ArrowLeft: -1 };

export type FilterOption = { label: string; value: string };

export type Segment = FilterOption & { id?: string; controls?: string };

/** The one row of choices in the portal, whether it narrows a listing or switches a page's panel.
 *  A filled pill slides under the picked one and nothing else moves: selection is drawn in surface
 *  and colour, never in weight or an underline. Bolding remeasures the text, so every segment
 *  after the picked one slides sideways as the member moves along the row; an underline states a
 *  boundary the row does not have, and the same act then looks like two different controls
 *  depending on which screen it is on. It carries the roving tabindex `role="tablist"` requires:
 *  one press reaches the row, then left and right move the choice and carry the focus, wrapping at
 *  each end.
 *
 *  The pill is drawn only once the row has been measured. It has no resting place to be drawn in
 *  before that — a pill given zeros for its first paint slides out of the row's left corner and
 *  grows into the picked choice, which states a change of choice on a screen the member has only
 *  just opened. Mounting it already placed skips that: a transition animates a box that moves, and
 *  a box drawn where it belongs the first time has not moved. */
export function Segmented({
  label,
  segments,
  value,
  onPick,
}: {
  label: string;
  segments: Segment[];
  value: string;
  onPick: (value: string) => void;
}) {
  const list = useRef<HTMLDivElement>(null);
  const [pill, setPill] = useState<{
    left: number;
    top: number;
    width: number;
    height: number;
  } | null>(null);
  useLayoutEffect(() => {
    const row = list.current;
    const active = row?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (!row || !active) return;
    setPill({
      left: active.offsetLeft,
      top: active.offsetTop,
      width: active.offsetWidth,
      height: active.offsetHeight,
    });
    const before = active.offsetLeft - row.scrollLeft;
    const after = active.offsetLeft + active.offsetWidth - row.scrollLeft - row.clientWidth;
    if (before < 0) row.scrollLeft += before;
    else if (after > 0) row.scrollLeft += after;
  }, [value, segments]);
  return (
    <div
      data-slot="segmented"
      ref={list}
      role="tablist"
      aria-label={label}
      className={cn(
        "relative flex items-stretch gap-hair overflow-x-auto",
        "[scrollbar-width:none] [&::-webkit-scrollbar]:hidden",
        "max-narrow:flex-wrap",
      )}
    >
      {pill ? (
        <span
          aria-hidden
          className={cn(
            "absolute rounded-full bg-fill",
            "transition-[left,top,width,height] duration-100 ease-control motion-reduce:transition-none",
          )}
          style={{ left: pill.left, top: pill.top, width: pill.width, height: pill.height }}
        />
      ) : null}
      {segments.map((segment, index) => {
        const active = value === segment.value;
        return (
          <button
            key={segment.value || "all"}
            id={segment.id}
            type="button"
            role="tab"
            aria-selected={active}
            aria-controls={segment.controls}
            tabIndex={active ? 0 : -1}
            onClick={() => onPick(segment.value)}
            onKeyDown={(event) => {
              const step = STEP[event.key];
              if (!step) return;
              event.preventDefault();
              const next = (index + step + segments.length) % segments.length;
              onPick(segments[next].value);
              event.currentTarget.parentElement
                ?.querySelectorAll<HTMLButtonElement>('[role="tab"]')
                [next]?.focus();
            }}
            className={cn(
              "relative h-(--size-control) shrink-0 rounded-full border-0 bg-transparent px-2xl",
              "text-label",
              "transition-[color] duration-100 ease-control motion-reduce:transition-none",
              active ? "text-ink" : "text-ink-soft hover:text-ink",
            )}
          >
            {segment.label}
          </button>
        );
      })}
    </div>
  );
}

export function Filter({
  options,
  value,
  onChange,
  all = true,
}: {
  options: FilterOption[];
  value: string;
  onChange: (value: string) => void;
  all?: boolean;
}) {
  return (
    <Segmented
      label="Filter"
      segments={all ? [{ label: "All", value: "" }, ...options] : options}
      value={value}
      onPick={onChange}
    />
  );
}
