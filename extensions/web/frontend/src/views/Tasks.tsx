import { useState } from "react";

import { Sheet } from "@/components/ui/sheet";
import { ObjectDetail, ObjectPane, objectAt, slotOf } from "@/kernel/objects";
import type { Placement } from "@/kernel/pager";
import { PanelEmpty } from "@/kernel/panel";

const KINDS = [
  { kind: "scheduled_task", label: "Scheduled" },
  { kind: "source_trigger", label: "Triggers" },
];

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
