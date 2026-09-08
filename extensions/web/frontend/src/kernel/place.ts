import { useEffect, useRef, useState } from "react";

import type { Placement } from "@/kernel/pager";
import { mergePlace, type PlaceStep, type WorkspacePlace } from "@/lib/route";

type Outcome = { view: string; notice: string | undefined; acts: number };

export function usePlaceRecorder({
  view,
  place,
  remountOnPlace,
  onPlace,
}: {
  view: string;
  place: WorkspacePlace;
  remountOnPlace: boolean;
  onPlace: (place: WorkspacePlace, step: PlaceStep) => void;
}): { key: string; merged: Placement; record: (patch: Placement) => void } {
  const [outcome, setOutcome] = useState<Outcome>({ view, notice: undefined, acts: 0 });
  if (outcome.view !== view) setOutcome({ view, notice: undefined, acts: 0 });

  const key = remountOnPlace
    ? [view, place.kind ?? "", place.after ?? "", String(outcome.acts)].join("|")
    : view;
  const merged: Placement = { ...place, notice: outcome.notice };

  const live = useRef(place);
  live.current = place;
  const pushedOpen = useRef<string[]>([]);
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
    pushedOpen.current = [];
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
    const next = mergePlace(held, patch);
    const moved =
      (next.after !== undefined && next.after !== held.after) ||
      (next.kind !== undefined && next.kind !== held.kind);
    const heldOpens = held.opens ?? [];
    const nextOpens = next.opens ?? [];
    const added = nextOpens.filter((id) => !heldOpens.includes(id));
    const dropped = heldOpens.filter((id) => !nextOpens.includes(id));
    const opened = added.length === 1 && dropped.length === 0;
    const closed = dropped.length === 1 && added.length === 0;
    const newest = closed && !moved && dropped[0] === pushedOpen.current.at(-1);
    const step: PlaceStep =
      newest ? "back" : opened || moved ? "push" : "replace";
    if (opened) pushedOpen.current = [...pushedOpen.current, added[0]];
    else if (step === "back") pushedOpen.current = pushedOpen.current.slice(0, -1);
    else if (dropped.length || moved) pushedOpen.current = [];
    onPlace(next, step);
  };

  return { key, merged, record };
}
