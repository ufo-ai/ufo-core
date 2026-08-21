import { useBeside } from "@/kernel/beside";
import { ObjectDetail, ObjectPane, type ObjectAddress } from "@/kernel/objects";
import type { Placement } from "@/kernel/pager";
import { PageHeader } from "@/kernel/pane";

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

const OBJECT_PREFIX = "object/";

function objectAt(open: string | undefined): ObjectAddress | null {
  if (!open?.startsWith(OBJECT_PREFIX)) return null;
  const rest = open.slice(OBJECT_PREFIX.length);
  const cut = rest.indexOf("/");
  return cut < 0 ? null : { kind: rest.slice(0, cut), name: rest.slice(cut + 1) };
}

/** The workspace's standing orders. A record the route names — which is what search hands over —
 *  opens beside the listings; `place.agent` remembers whose namespace it lives in, since they cross
 *  agents. */
export function Tasks({
  title,
  place,
  onPlace,
}: {
  title?: string;
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const at = objectAt(place.open);
  const owner = place.agent ?? null;
  const detail = useBeside(
    at !== null && at.name !== null && owner !== null ? (
      <ObjectDetail
        key={owner + "/" + at.kind + "/" + at.name}
        agentId={owner}
        kind={at.kind}
        name={at.name}
        onOpen={(next) => onPlace({ open: OBJECT_PREFIX + next.kind + "/" + (next.name ?? "") })}
        onBack={() => onPlace({ open: undefined, agent: undefined })}
      />
    ) : null,
    () => onPlace({ open: undefined, agent: undefined }),
  );
  return (
    <>
      {title ? <PageHeader title={title} /> : null}
      {KINDS.map((held) => (
        <ObjectPane key={held.kind} agentId={null} kind={held.kind} section={held.label} />
      ))}
      {detail}
    </>
  );
}
