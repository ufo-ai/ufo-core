import { useEffect, useRef, useState } from "react";

import { LISTING_CONTROLS } from "@/kernel/listing";
import type { Placement } from "@/kernel/pager";
import type { WorkspaceTab } from "@/lib/route";
import { WORKSPACE_VIEWS } from "@/views/registry";

type Placed = { view: WorkspaceTab; place: Placement; acts: number };

export function Workspace({ view }: { view: WorkspaceTab }) {
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
    <main className="flex flex-col gap-3xl overflow-y-auto p-2xl" data-testid="workspace">
      <Registered key={key} view={view} place={place} onPlace={record} />
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
