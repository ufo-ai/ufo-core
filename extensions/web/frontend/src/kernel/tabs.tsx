import type { ComponentProps } from "react";

import { Segmented } from "@/components/ui/filter";
import { cn } from "@/lib/cn";
import { COLUMN } from "@/kernel/pane";

function tabId(group: string, tab: string) {
  return group + "-" + tab + "-tab";
}

function panelId(group: string, tab: string) {
  return group + "-" + tab + "-panel";
}

/** A destination's tabs as the one segmented row the portal picks with — the same control a
 *  listing narrows itself by, so a member learns one control and reads it everywhere. Selection is
 *  the filled pill; there is no underline and no rule, because a line under a page's tabs states a
 *  boundary between its name and its contents that the surface does not have. */
export function TabRow<T extends string>({
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
    <Segmented
      label="Tabs"
      segments={tabs.map((name) => ({
        value: name,
        label: label(name),
        id: tabId(group, name),
        controls: panelId(group, name),
      }))}
      value={current}
      onPick={(value) => onPick(value as T)}
    />
  );
}

/** The same row standing on a page: in the pane's column, a band clear of the title above and the
 *  panel below, with its leading pill pulled out to the column's line so the first tab starts
 *  where the title does. */
export function TabStrip<T extends string>(props: Parameters<typeof TabRow<T>>[0]) {
  return (
    <div className="pt-2xl pb-2xl">
      <div className={cn(COLUMN, "px-2xl")}>
        <div className="-ml-2xl">
          <TabRow {...props} />
        </div>
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
