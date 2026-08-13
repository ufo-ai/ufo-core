import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";
import { COLUMN } from "@/kernel/pane";

const STEP: Record<string, number> = { ArrowRight: 1, ArrowLeft: -1 };

function tabId(group: string, tab: string) {
  return group + "-" + tab + "-tab";
}

function panelId(group: string, tab: string) {
  return group + "-" + tab + "-panel";
}

export function TabStrip<T extends string>({
  group,
  tabs,
  current,
  label,
  onPick,
}: {
  group: string;
  tabs: readonly T[];
  current: T;
  label: (tab: T) => string;
  onPick: (tab: T) => void;
}) {
  return (
    <div className="border-b border-edge">
      <div role="tablist" className={cn(COLUMN, "flex flex-wrap gap-2xs px-xs pt-xs")}>
        {tabs.map((name, index) => (
          <button
            key={name}
            id={tabId(group, name)}
            type="button"
            role="tab"
            aria-selected={name === current}
            aria-controls={panelId(group, name)}
            tabIndex={name === current ? 0 : -1}
            onClick={() => onPick(name)}
            onKeyDown={(event) => {
              const step = STEP[event.key];
              if (!step) return;
              event.preventDefault();
              const next = tabs[(index + step + tabs.length) % tabs.length];
              onPick(next);
              document.getElementById(tabId(group, next))?.focus();
            }}
            className={cn(
              "border-0 border-b-(length:--marker-width) border-b-transparent bg-transparent",
              "px-md py-xs text-inherit opacity-(--muted-soft)",
              name === current && "border-b-ink opacity-100",
            )}
          >
            {label(name)}
          </button>
        ))}
      </div>
    </div>
  );
}

export function TabPanel({
  group,
  current,
  ...props
}: ComponentProps<"div"> & { group: string; current: string }) {
  return (
    <div
      role="tabpanel"
      id={panelId(group, current)}
      aria-labelledby={tabId(group, current)}
      tabIndex={0}
      {...props}
    />
  );
}
