import { useLayoutEffect, useRef, useState } from "react";

import { cn } from "@/lib/cn";

const STEP: Record<string, number> = { ArrowRight: 1, ArrowLeft: -1 };

export type FilterOption = { label: string; value: string };

/** Narrows a collection to one of its kinds. The empty value is every row, so the caller passes
 *  only the narrowings and the tablist supplies its own `All` — except where the options are the
 *  whole collection between them, which `all={false}` states and which then draws no unreachable
 *  tab. The picked tab sits on a pill that slides beneath the labels — the tabs themselves never
 *  change surface or weight, so choosing one cannot rewrap the row under the pointer. */
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
  const entries = all ? [{ label: "All", value: "" }, ...options] : options;
  const list = useRef<HTMLDivElement>(null);
  const [pill, setPill] = useState({ left: 0, width: 0 });
  useLayoutEffect(() => {
    const active = list.current?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (active) setPill({ left: active.offsetLeft, width: active.offsetWidth });
  }, [value, options]);
  return (
    <div
      ref={list}
      role="tablist"
      aria-label="Filter"
      className="relative flex items-stretch gap-hair"
    >
      <span
        aria-hidden
        className="absolute inset-y-0 rounded-control bg-fill-subtle transition-[left,width] duration-100 ease-control motion-reduce:transition-none"
        style={{ left: pill.left, width: pill.width }}
      />
      {entries.map((entry, index) => {
        const active = value === entry.value;
        return (
          <button
            key={entry.value || "all"}
            type="button"
            role="tab"
            aria-selected={active}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(entry.value)}
            onKeyDown={(event) => {
              const step = STEP[event.key];
              if (!step) return;
              event.preventDefault();
              const next = (index + step + entries.length) % entries.length;
              onChange(entries[next].value);
              event.currentTarget.parentElement
                ?.querySelectorAll<HTMLButtonElement>('[role="tab"]')
                [next]?.focus();
            }}
            className={cn(
              "relative rounded-control border-0 bg-transparent px-lg py-xs text-ui",
              "transition-[opacity] duration-100 ease-control motion-reduce:transition-none",
              active ? "opacity-100" : "opacity-(--muted-soft) hover:opacity-100",
            )}
          >
            {entry.label}
          </button>
        );
      })}
    </div>
  );
}
