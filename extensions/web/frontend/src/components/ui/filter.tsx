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
  const [pill, setPill] = useState({ left: 0, width: 0 });
  useLayoutEffect(() => {
    const active = list.current?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (active) setPill({ left: active.offsetLeft, width: active.offsetWidth });
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
      )}
    >
      <span
        aria-hidden
        className={cn(
          "absolute inset-y-0 rounded-full bg-fill-subtle",
          "transition-[left,width] duration-100 ease-control motion-reduce:transition-none",
        )}
        style={{ left: pill.left, width: pill.width }}
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
              "transition-[opacity] duration-100 ease-control motion-reduce:transition-none",
              active ? "opacity-100" : "opacity-(--opacity-muted-soft) hover:opacity-100",
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
