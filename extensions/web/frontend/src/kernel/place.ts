import { useEffect, useRef, useState } from "react";

import type { Placement } from "@/kernel/pager";
import type { PlaceStep, WorkspacePlace } from "@/lib/route";

type Outcome = { view: string; notice: string | undefined; acts: number };

/** The place bookkeeping every pane that holds a listing shares: the outcome notice a mutation
 *  leaves, the remount key that carries it to the next mount, and the push/replace/back step a
 *  place change earns. One owner, so the workspace's tabs and a top-level section cannot disagree
 *  about what opening a row does to history. */
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
      agent: "agent" in patch ? patch.agent : held.agent,
    };
    const moved =
      (next.after !== undefined && next.after !== held.after) ||
      (next.kind !== undefined && next.kind !== held.kind) ||
      (next.agent !== undefined && next.agent !== held.agent);
    const opened = next.open !== undefined && held.open === undefined;
    const closed = !moved && next.open === undefined && held.open !== undefined;
    const step: PlaceStep =
      closed && pushedOpen.current ? "back" : opened || moved ? "push" : "replace";
    if (opened) pushedOpen.current = true;
    if (closed || moved) pushedOpen.current = false;
    onPlace(next, step);
  };

  return { key, merged, record };
}
