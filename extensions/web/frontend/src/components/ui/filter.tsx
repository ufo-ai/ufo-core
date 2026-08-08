import { cn } from "@/lib/cn";

const STEP: Record<string, number> = { ArrowRight: 1, ArrowLeft: -1 };

export type FilterOption = { label: string; value: string };

/** Narrows a collection to one of its kinds. The empty value is every row, so the caller passes
 *  only the narrowings and the tablist supplies its own `All`. The picked tab takes a card and
 *  full opacity and nothing else — its weight is the weight of every other tab, so choosing one
 *  cannot rewrap the row under the pointer. */
export function Filter({
  options,
  value,
  onChange,
}: {
  options: FilterOption[];
  value: string;
  onChange: (value: string) => void;
}) {
  const entries = [{ label: "All", value: "" }, ...options];
  return (
    <div
      role="tablist"
      aria-label="Filter"
      className="flex items-stretch gap-hair rounded-panel bg-fill-subtle p-hair"
    >
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
              "rounded-control border border-transparent bg-transparent px-lg text-ui",
              "opacity-(--muted-soft)",
              active && "border-edge bg-surface opacity-100",
            )}
          >
            {entry.label}
          </button>
        );
      })}
    </div>
  );
}
