import type { ComponentProps } from "react";

import { Segmented } from "@/components/ui/filter";

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
