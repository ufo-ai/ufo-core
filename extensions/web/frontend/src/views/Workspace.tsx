import { useState } from "react";

import { Artifacts } from "@/views/Artifacts";
import { Memory } from "@/views/Memory";
import { Sources } from "@/views/Sources";
import { Team } from "@/views/Team";
import { WorkspaceCredentials } from "@/views/WorkspaceCredentials";
import { WorkspaceUsage } from "@/views/Usage";
import { Td } from "@/components/ui/table";
import { Panel, usePanelRead } from "@/kernel/panel";
import { DataTable } from "@/kernel/table";
import { type Placement } from "@/kernel/pager";
import type { WorkspaceTab } from "@/lib/route";

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
  return (
    <Panel
      state={state}
      empty={(payload) => (payload.available ? null : "No sites extension is installed.")}
    >
      {(payload) => (
        <DataTable
          columns={["site", "summary"]}
          rows={payload.sites}
          rowKey={(site) => site.name}
          empty="No sites are hosted."
        >
          {(site) => (
            <>
              <Td>{site.name}</Td>
              <Td>{site.summary}</Td>
            </>
          )}
        </DataTable>
      )}
    </Panel>
  );
}
