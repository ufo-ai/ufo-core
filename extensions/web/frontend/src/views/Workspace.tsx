import { useEffect, useRef, useState } from "react";

import { LISTING_CONTROLS } from "@/kernel/listing";
import type { Placement } from "@/kernel/pager";
import { WORKSPACE_TABS, type WorkspaceTab } from "@/lib/route";
import { cn } from "@/lib/cn";
import { WORKSPACE_VIEWS } from "@/views/registry";

type Placed = { view: WorkspaceTab; place: Placement; acts: number };

export function Workspace({
  view,
  onView,
}: {
  view: WorkspaceTab;
  onView: (view: WorkspaceTab) => void;
}) {
  const [placed, setPlaced] = useState<Placed>({ view, place: {}, acts: 0 });
  const shown = useRef(view);
  shown.current = view;
  useEffect(() => () => LISTING_CONTROLS.clear(), []);
  if (placed.view !== view) {
    LISTING_CONTROLS.clear();
    setPlaced({ view, place: {}, acts: 0 });
  }

  const registered = WORKSPACE_VIEWS[view];
  const key = registered.remountOnPlace
    ? [view, placed.place.kind ?? "", placed.place.after ?? "", String(placed.acts)].join("|")
    : view;

  const place = placed.place;
  const record = (next: Placement) => {
    if (shown.current !== view) return;
    setPlaced((prev) => ({ view, place: next, acts: prev.acts + 1 }));
  };

  return (
    <main className="flex min-h-0 min-w-0 flex-col">
      <div className="flex items-baseline gap-md px-2xl pt-lg">
        <h1 className="m-0 text-title font-strong">Workspace</h1>
      </div>
      <div role="tablist" className="flex flex-wrap gap-2xs border-b border-edge px-lg pt-xs">
        {WORKSPACE_TABS.map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            aria-selected={name === view}
            onClick={() => onView(name)}
            className={cn(
              "border-0 border-b-(length:--marker-width) border-b-transparent bg-transparent",
              "px-md py-xs text-inherit opacity-(--muted-soft)",
              name === view && "border-b-ink font-strong opacity-100",
            )}
          >
            {WORKSPACE_VIEWS[name].label}
          </button>
        ))}
      </div>
      <div
        className="flex flex-1 flex-col gap-3xl overflow-y-auto p-2xl"
        data-testid="workspace"
      >
        <Registered key={key} view={view} place={place} onPlace={record} />
      </div>
    </main>
  );
}

function Registered({
  view,
  place,
  onPlace,
}: {
  view: WorkspaceTab;
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  return WORKSPACE_VIEWS[view].render(place, onPlace);
}
