import { useLayoutEffect, useRef, useState } from "react";

import { cn } from "@/lib/cn";

const STEP: Record<string, number> = { ArrowRight: 1, ArrowLeft: -1 };

export type FilterOption = { label: string; value: string };

/** One choice in a segmented row: what it is worth, what it says, and — where the row switches a
 *  panel rather than narrowing a list — the ids that tie the tab to what it shows. */
export type Segment = FilterOption & { id?: string; controls?: string };

/** The one row of choices in the portal, whether it narrows a listing or switches a page's panel.
 *  A filled pill slides under the picked one and nothing else moves: selection is drawn in surface
 *  and colour, never in weight or an underline. Bolding remeasures the text, so every segment
 *  after the picked one slides sideways as the member moves along the row; an underline states a
 *  boundary the row does not have, and the same act then looks like two different controls
 *  depending on which screen it is on. It carries the roving tabindex `role="tablist"` requires:
 *  one press reaches the row, then left and right move the choice and carry the focus, wrapping at
 *  each end. */
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
  const [pill, setPill] = useState({ left: 0, top: 0, width: 0, height: 0 });
  useLayoutEffect(() => {
    const row = list.current;
    const active = row?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (!row || !active) return;
    /* The pill takes the picked choice's whole box, line included: a phone wraps the row, and a
       pill placed by its left edge alone would sit on the first line under a choice on the second. */
    setPill({
      left: active.offsetLeft,
      top: active.offsetTop,
      width: active.offsetWidth,
      height: active.offsetHeight,
    });
    /* A row wider than the screen scrolls, and the pill is the only mark of which choice is
       picked — so the picked one is brought into the row's own view rather than left past an
       edge, where the row states nothing at all. */
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
        /* A phone shows every choice whole, on as many lines as that takes — the treatment the top
           nav and the pane's own act row already take there. A choice left half past a scrolling
           edge is both unreadable and too small to press. */
        "max-narrow:flex-wrap",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "absolute rounded-full bg-fill",
          "transition-[left,top,width,height] duration-100 ease-control motion-reduce:transition-none",
        )}
        style={{ left: pill.left, top: pill.top, width: pill.width, height: pill.height }}
      />
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

/** Narrows a collection to one of its kinds. The empty value is every row, so the caller passes
 *  only the narrowings and the row supplies its own `All` — except where the options are the whole
 *  collection between them, which `all={false}` states and which then draws no unreachable tab. */
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
