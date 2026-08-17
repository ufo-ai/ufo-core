import { Button } from "@/components/ui/button";
import type { WorkspacePlace } from "@/lib/route";

/** Where a pane stands, and what the change to it was: the outcome a mutation left, and whether the
 *  record the place named was taken by another panel instead of shut by the member. A place clearing
 *  a taken record states it, because the way out the member pressed is the only close that steps the
 *  route back off the entry the record was opened on. */
export type Placement = WorkspacePlace & { notice?: string; displaced?: boolean };

export function Pager({
  payload,
  onPlace,
}: {
  payload: { newer?: string | null; older?: string | null };
  onPlace: (place: Placement) => void;
}) {
  const steps: [string, string | null | undefined][] = [
    ["Newer", payload.newer],
    ["Older", payload.older],
  ];
  return (
    <div className="flex gap-xs">
      {steps.map(([label, cursor]) =>
        cursor ? (
          <Button
            key={label}
            variant="row"
            onClick={() => onPlace({ after: cursor, open: undefined })}
          >
            {label}
          </Button>
        ) : null,
      )}
    </div>
  );
}
