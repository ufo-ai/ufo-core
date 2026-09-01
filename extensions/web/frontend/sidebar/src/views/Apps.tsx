import { createContext, useContext, useState, type FormEvent, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/field";
import { Filter } from "@/components/ui/filter";
import { ACTS, Lede, Td, TdActs, TdFact } from "@/components/ui/table";
import type { Placement } from "@/kernel/pager";
import { OutcomeNotice, QUIET, outcomeNotice, Section, type NoticeState } from "@/kernel/panel";
import { DataTable } from "@/kernel/table";
import { AgentIcon } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";
import { postObjectAction } from "@/lib/api";
import { useMainAgent } from "@/lib/mainAgent";
import { Moment } from "@/lib/moments";
import { openAgent } from "@/lib/router";
import type { Agent, ArchivedApp } from "@/lib/types";

type AppsState = {
  agents: Agent[];
  archived: ArchivedApp[];
  onRestored: () => void;
};

const AppsContext = createContext<AppsState | null>(null);

export function AppsProvider({
  agents,
  archived,
  onRestored,
  children,
}: AppsState & { children: ReactNode }) {
  return (
    <AppsContext.Provider value={{ agents, archived, onRestored }}>
      {children}
    </AppsContext.Provider>
  );
}

const CREATED_BY_ME = "Created by me";
const ARCHIVED = "Archived";
const FILTERS = [
  { label: CREATED_BY_ME, value: CREATED_BY_ME },
  { label: ARCHIVED, value: ARCHIVED },
];

/** The workspace's apps: every app the member reaches, narrowed to their own or to the archived
 *  ones by the filter the address carries. A live row opens the app; an archived row's one act is
 *  the restore. */
export function Apps({
  place,
  onPlace,
}: {
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const state = useContext(AppsContext);
  if (!state) throw new Error("AppsProvider is required");
  const { agents, archived, onRestored } = state;
  const picked = place.chip ?? "";
  const bar = (
    <Filter
      options={FILTERS}
      value={picked}
      onChange={(value) => onPlace({ chip: value || undefined })}
    />
  );
  if (picked === ARCHIVED) {
    return (
      <Section bar={bar}>
        <ArchivedTable apps={archived} onRestored={onRestored} />
      </Section>
    );
  }
  const apps = picked === CREATED_BY_ME ? agents.filter((agent) => agent.mine) : agents;
  return (
    <Section bar={bar}>
      <DataTable
        columns={["Application", "Purpose"]}
        rows={apps}
        rowKey={(app) => app.id}
        empty={picked === CREATED_BY_ME ? "You haven't created an app." : "No apps."}
        open={(app) => () => openAgent(app.id)}
      >
        {(app) => (
          <>
            <Td>
              <Lede mark={<AgentIcon name={app.icon} />}>{agentName(app.name)}</Lede>
            </Td>
            <Td className="text-ink-soft">{app.purpose ?? ""}</Td>
          </>
        )}
      </DataTable>
    </Section>
  );
}

function ArchivedTable({ apps, onRestored }: { apps: ArchivedApp[]; onRestored: () => void }) {
  const mainAgent = useMainAgent();
  const [restoring, setRestoring] = useState<ArchivedApp | null>(null);
  const [name, setName] = useState("");
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const [busy, setBusy] = useState(false);

  function open(app: ArchivedApp) {
    setRestoring(app);
    setName(app.name);
    setNotice(QUIET);
  }

  async function restore(event: FormEvent) {
    event.preventDefault();
    if (!restoring || !mainAgent || busy) return;
    setBusy(true);
    const outcome = await postObjectAction(
      mainAgent.id,
      { kind: "agent", name: restoring.object, action: "restore_application" },
      { new_name: name },
    );
    setBusy(false);
    if (!outcome.applied) {
      setNotice(outcomeNotice(outcome));
      return;
    }
    setRestoring(null);
    onRestored();
  }

  return (
    <>
      <DataTable
        columns={["Application", { label: "Archived", fact: true }, { label: "", fact: true }]}
        rows={apps}
        rowKey={(app) => app.id}
        empty="No apps are archived."
      >
        {(app) => (
          <>
            <Td>
              <Lede mark={<AgentIcon name={app.icon} />}>{agentName(app.name)}</Lede>
            </Td>
            <TdFact>
              <Moment at={app.archived_at} />
            </TdFact>
            <TdActs>
              <div className={ACTS}>
                <Button variant="row" onClick={() => open(app)}>
                  Restore
                </Button>
              </div>
            </TdActs>
          </>
        )}
      </DataTable>
      <Dialog open={restoring !== null} onOpenChange={(shown) => (shown ? null : setRestoring(null))}>
        <DialogContent className="w-settings" aria-describedby={undefined}>
          <DialogHeader>
            <DialogTitle>Restore {restoring ? agentName(restoring.name) : ""}</DialogTitle>
          </DialogHeader>
          <form onSubmit={restore}>
            <Field label="Name" htmlFor="restore-name">
              <Input
                id="restore-name"
                required
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </Field>
            <OutcomeNotice state={notice} />
            <div className="mt-lg flex justify-end">
              <Button type="submit" variant="send" size="bar" busy={busy}>
                Restore
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
