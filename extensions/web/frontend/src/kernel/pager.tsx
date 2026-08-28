import { Button } from "@/components/ui/button";
import type { WorkspacePlace } from "@/lib/route";

/** Where a pane stands, and what the change to it was: the place itself, and the outcome a mutation
 *  left for the mount that comes after it. */
export type Placement = WorkspacePlace & { notice?: string };

/** The Newer and Older steps under a listing, drawn only for the cursors the payload holds. */
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
            onClick={() => onPlace({ after: cursor, opens: undefined })}
          >
            {label}
          </Button>
        ) : null,
      )}
    </div>
  );
}
