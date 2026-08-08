import { useEffect, useRef, useState } from "react";

import type { Placement } from "@/kernel/pager";
import type { PlaceStep } from "@/lib/route";
import { WORKSPACE_TABS, type WorkspacePlace, type WorkspaceTab } from "@/lib/route";
import { TabPanel, TabStrip } from "@/kernel/tabs";
import { WORKSPACE_VIEWS } from "@/views/registry";

type Outcome = { view: WorkspaceTab; notice: string | undefined; acts: number };

export function Workspace({
  view,
  place,
  onPlace,
}: {
  view: WorkspaceTab;
  place: WorkspacePlace;
  onPlace: (view: WorkspaceTab, place: WorkspacePlace, step: PlaceStep) => void;
}) {
  const [outcome, setOutcome] = useState<Outcome>({ view, notice: undefined, acts: 0 });
  if (outcome.view !== view) setOutcome({ view, notice: undefined, acts: 0 });

  const registered = WORKSPACE_VIEWS[view];
  const key = registered.remountOnPlace
    ? [view, place.kind ?? "", place.after ?? "", String(outcome.acts)].join("|")
    : view;

  const merged: Placement = { ...place, notice: outcome.notice };
  const live = useRef(place);
  live.current = place;
  const pushedOpen = useRef(false);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const epoch = useRef(0);
  const seen = useRef(view);
  if (seen.current !== view) {
    seen.current = view;
    epoch.current += 1;
    pushedOpen.current = false;
  }
  const issued = epoch.current;

  const record = (patch: Placement) => {
    if (!alive.current || issued !== epoch.current) return;
    if (patch.notice !== undefined) {
      setOutcome((prev) => ({ view, notice: patch.notice, acts: prev.acts + 1 }));
    } else if (outcome.notice !== undefined) {
      setOutcome((prev) => ({ ...prev, notice: undefined }));
    }
    const held = live.current;
    const next: WorkspacePlace = {
      kind: "kind" in patch ? patch.kind : held.kind,
      after: "after" in patch ? patch.after : held.after,
      q: "q" in patch ? patch.q : held.q,
      chip: "chip" in patch ? patch.chip : held.chip,
      open: "open" in patch ? patch.open : held.open,
    };
    const moved =
      (next.after !== undefined && next.after !== held.after) ||
      (next.kind !== undefined && next.kind !== held.kind);
    const opened = next.open !== undefined && held.open === undefined;
    const closed = !moved && next.open === undefined && held.open !== undefined;
    const step: PlaceStep =
      closed && pushedOpen.current ? "back" : opened || moved ? "push" : "replace";
    if (opened) pushedOpen.current = true;
    if (closed || moved) pushedOpen.current = false;
    onPlace(view, next, step);
  };

  return (
    <main className="flex min-h-0 min-w-0 flex-col">
      <div className="flex items-baseline gap-md px-2xl pt-lg">
        <h1 className="m-0 text-title font-strong">Workspace</h1>
      </div>
      <TabStrip
        group="workspace"
        tabs={WORKSPACE_TABS}
        current={view}
        label={(name) => WORKSPACE_VIEWS[name].label}
        onPick={(name) => onPlace(name, {}, "push")}
      />
      <TabPanel
        group="workspace"
        current={view}
        className="flex flex-1 flex-col overflow-y-auto p-2xl"
        data-testid="workspace"
      >
        <Registered key={key} view={view} place={merged} onPlace={record} />
      </TabPanel>
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
