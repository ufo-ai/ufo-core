import { ObjectPane } from "@/kernel/objects";
import type { Placement } from "@/kernel/pager";

const SCHEDULED_TASK_KIND = "scheduled_task";
const SOURCE_TRIGGER_KIND = "source_trigger";

export function Scheduled({
  place,
  onPlace,
}: {
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  return (
    <ObjectPane
      agentId={null}
      kind={SCHEDULED_TASK_KIND}
      opens={place.opens ?? []}
      onPlace={onPlace}
    />
  );
}

export function Triggers({
  place,
  onPlace,
}: {
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  return (
    <ObjectPane
      agentId={null}
      kind={SOURCE_TRIGGER_KIND}
      opens={place.opens ?? []}
      onPlace={onPlace}
    />
  );
}
