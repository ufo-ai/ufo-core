import { useState } from "react";

import { Sheet } from "@/components/ui/sheet";
import { ObjectDetail, ObjectPane, objectAt, slotOf } from "@/kernel/objects";
import type { Placement } from "@/kernel/pager";
import { PanelEmpty } from "@/kernel/panel";

/** What is armed to run an agent when nobody is typing: a clock, or a source that changed. The two
 *  are one destination because a member asking what stands ready here asks one question, and an
 *  answer split across two screens is one they have to know to look for twice.
 *
 *  They stand as two listings rather than one, because they are two kinds with almost nothing in
 *  common to put in a column — a schedule and a next run against a source and where it came in.
 *  Merged, every row would carry a column the other kind leaves empty; stacked, each keeps the
 *  columns, order, search and pager its own kind declares, and the page still answers the one
 *  question whole. Neither needs a control to reach it: they are both already on the screen.
 *
 *  What those standing orders have already done is the radar's answer, not this one: a member here
 *  is reading or changing what will happen, not what did. */
const KINDS = [
  { kind: "scheduled_task", label: "Scheduled" },
  { kind: "source_trigger", label: "Triggers" },
];

/** The workspace's standing orders. Both listings open their selected record in the page's one
 *  sheet. Closing it reads the listings again, so a changed or deleted row is stated as it is. */
export function Tasks({
  place,
  onPlace,
}: {
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const [generation, setGeneration] = useState(0);
  const selected = place.opens?.at(-1) ?? null;
  const at = selected === null ? null : objectAt(selected);
  const close = () => {
    onPlace({ opens: [] });
    setGeneration((count) => count + 1);
  };
  return (
    <>
      {KINDS.map((held) => (
        <ObjectPane
          key={held.kind + "/" + generation}
          agentId={null}
          kind={held.kind}
          section={held.label}
          opens={selected === null ? [] : [selected]}
          onPlace={(next) => onPlace({ ...next, opens: next.opens?.slice(-1) })}
        />
      ))}
      {selected && at === null ? (
        <Sheet open title={selected} onClose={close}>
          <PanelEmpty>That item is not on this page.</PanelEmpty>
        </Sheet>
      ) : at !== null ? (
        <ObjectDetail
          key={selected}
          agentId={at.agent}
          kind={at.kind}
          name={at.name}
          onOpen={(next) => onPlace({ opens: [slotOf(next)] })}
          onBack={close}
        />
      ) : null}
    </>
  );
}
