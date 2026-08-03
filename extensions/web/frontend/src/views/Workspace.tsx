import { useRef, useState } from "react";

import type { Placement } from "@/kernel/pager";
import type { PlaceStep } from "@/lib/route";
import { WORKSPACE_TABS, type WorkspacePlace, type WorkspaceTab } from "@/lib/route";
import { cn } from "@/lib/cn";
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
  const shown = useRef(view);
  shown.current = view;
  if (outcome.view !== view) setOutcome({ view, notice: undefined, acts: 0 });

  const registered = WORKSPACE_VIEWS[view];
  const key = registered.remountOnPlace
    ? [view, place.kind ?? "", place.after ?? "", String(outcome.acts)].join("|")
    : view;

  const merged: Placement = { ...place, notice: outcome.notice };
  const live = useRef(place);
  live.current = place;
  const pushedOpen = useRef(false);

  /** A view names only the keys it moves; the rest ride the live place, so a placement that
   *  resolves after the member typed does not overwrite what they typed. Opening a row, paging,
   *  and narrowing a kind are places to come back from; closing unwinds the entry opening
   *  pushed, and a filter never becomes an entry at all. */
  const record = (patch: Placement) => {
    if (shown.current !== view) return;
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
    const opening = next.open !== undefined && next.open !== held.open;
    const closing = next.open === undefined && held.open !== undefined;
    const stepped =
      opening ||
      (next.after !== undefined && next.after !== held.after) ||
      (next.kind !== undefined && next.kind !== held.kind);
    const step: PlaceStep =
      closing && pushedOpen.current ? "back" : stepped ? "push" : "replace";
    pushedOpen.current = opening;
    onPlace(view, next, step);
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
            onClick={() => onPlace(name, {}, "push")}
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
        <Registered key={key} view={view} place={merged} onPlace={record} />
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
