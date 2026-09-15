import { Button } from "@/components/ui/button";
import type { WorkspacePlace } from "@/lib/route";

export type Placement = WorkspacePlace & { notice?: string };

export type Paging = {
  payload: { newer?: string | null; older?: string | null };
  after?: string;
  onPlace: (place: Placement) => void;
};

/** The two steps a listing offers, or none at all where it fits on one page. The back step is named
 *  for where it lands: an envelope carrying a backward cursor steps to the page before, and one
 *  carrying none steps off the cursor it stands on, which is the listing's first page — `First`
 *  says so rather than promising a page the read cannot reach.
 *
 *  Whatever seats the steps asks this before it draws the seat — a footer row holding a pager that
 *  rendered nothing is a record's height of blank under every single-page table. */
export function pagerSteps({ payload, after }: Paging): [string, string | null][] | null {
  const back = payload.newer ?? (after ? "" : null);
  const steps: [string, string | null][] = [
    [payload.newer ? "Previous" : "First", back],
    ["Next", payload.older ?? null],
  ];
  return steps.every(([, cursor]) => cursor === null) ? null : steps;
}

/** The back and Next steps under a listing, each disabled where it has nowhere to go. The disabled
 *  step is drawn rather than dropped, so the pair keeps its place and its width while the member
 *  walks the pages instead of moving under their cursor on every press. */
export function Pager(paging: Paging) {
  const steps = pagerSteps(paging);
  if (steps === null) return null;
  return (
    <div className="flex justify-end gap-sm">
      {steps.map(([label, cursor]) => (
        <Button
          key={label}
          variant="outline"
          disabled={cursor === null}
          onClick={() => paging.onPlace({ after: cursor || undefined, opens: undefined })}
        >
          {label}
        </Button>
      ))}
    </div>
  );
}
