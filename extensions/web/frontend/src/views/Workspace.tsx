import { useState } from "react";

import { Artifacts } from "@/views/Artifacts";
import { Memory } from "@/views/Memory";
import { Sources } from "@/views/Sources";
import { Team } from "@/views/Team";
import { WorkspaceCredentials } from "@/views/WorkspaceCredentials";
import { WorkspaceUsage } from "@/views/Usage";
import { Table, Td, Th } from "@/components/ui/table";
import { PanelEmpty, usePanelRead } from "@/kernel/panel";
import type { WorkspaceTab } from "@/lib/route";

export type Placement = { kind?: string; after?: string; notice?: string };

export function Workspace({ view }: { view: WorkspaceTab }) {
  const [place, setPlace] = useState<Placement>({});
  const [acts, setActs] = useState(0);
  const key = [view, place.kind ?? "", place.after ?? "", String(acts)].join("|");

  const record = (next: Placement) => {
    setPlace(next);
    setActs((count) => count + 1);
  };

  return (
    <main className="flex flex-col gap-3xl overflow-y-auto p-2xl" data-testid="workspace">
      {view === "team" ? <Team key={key} place={place} onPlace={record} /> : null}
      {view === "sources" ? <Sources key={key} place={place} onPlace={record} /> : null}
      {view === "credentials" ? (
        <WorkspaceCredentials key={key} place={place} onPlace={record} />
      ) : null}
      {view === "memory" ? <Memory key={view} place={place} onPlace={record} /> : null}
      {view === "artifacts" ? <Artifacts key={key} place={place} onPlace={record} /> : null}
      {view === "sites" ? <Sites /> : null}
      {view === "usage" ? <WorkspaceUsage /> : null}
    </main>
  );
}

type Site = { name: string; summary: string };

function Sites() {
  const state = usePanelRead<{ available: boolean; sites: Site[] }>("/workspace/sites");
  if (state.phase === "loading") return null;
  if (state.phase === "failed") return <PanelEmpty>{state.message}</PanelEmpty>;
  if (!state.payload.available) return <PanelEmpty>No sites extension is installed.</PanelEmpty>;
  if (!state.payload.sites.length) return <PanelEmpty>No sites are hosted.</PanelEmpty>;
  return (
    <Table>
      <thead>
        <tr>
          {["site", "summary"].map((column) => (
            <Th key={column}>{column}</Th>
          ))}
        </tr>
      </thead>
      <tbody>
        {state.payload.sites.map((site) => (
          <tr key={site.name}>
            <Td>{site.name}</Td>
            <Td>{site.summary}</Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

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
