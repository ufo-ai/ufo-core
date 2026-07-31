export type Placement = { kind?: string; after?: string; notice?: string };

export function Pager({
  payload,
  place,
  onPlace,
}: {
  payload: { newer?: string | null; older?: string | null };
  place: Placement;
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
            onClick={() => onPlace({ kind: place.kind, after: cursor })}
            className="border border-edge-control rounded-control bg-transparent px-sm py-hair text-inherit"
          >
            {label}
          </button>
        ) : null,
      )}
    </div>
  );
}
