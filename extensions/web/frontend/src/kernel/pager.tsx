import type { WorkspacePlace } from "@/lib/route";

export type Placement = WorkspacePlace & { notice?: string };

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
    <div className="mb-lg flex gap-xs">
      {steps.map(([label, cursor]) =>
        cursor ? (
          <button
            key={label}
            type="button"
            onClick={() =>
              onPlace({ after: cursor, open: undefined })
            }
            className="border border-edge-control rounded-control bg-transparent px-sm py-hair text-inherit"
          >
            {label}
          </button>
        ) : null,
      )}
    </div>
  );
}
