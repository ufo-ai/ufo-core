import { createContext, useContext, useState, type FormEvent, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/field";
import { ACTS, Lede, Td, TdActs, TdFact } from "@/components/ui/table";
import { OutcomeNotice, QUIET, outcomeNotice, Section, type NoticeState } from "@/kernel/panel";
import { DataTable } from "@/kernel/table";
import { AgentIcon } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";
import { postIntent } from "@/lib/api";
import { useMainAgent } from "@/lib/mainAgent";
import { Moment } from "@/lib/moments";
import type { ArchivedApp } from "@/lib/types";

type ArchivedAppsState = {
  apps: ArchivedApp[];
  onRestored: () => void;
};

const ArchivedAppsContext = createContext<ArchivedAppsState | null>(null);

export function ArchivedAppsProvider({
  apps,
  onRestored,
  children,
}: ArchivedAppsState & { children: ReactNode }) {
  return (
    <ArchivedAppsContext.Provider value={{ apps, onRestored }}>
      {children}
    </ArchivedAppsContext.Provider>
  );
}

export function ArchivedApps() {
  const state = useContext(ArchivedAppsContext);
  const mainAgent = useMainAgent();
  const [restoring, setRestoring] = useState<ArchivedApp | null>(null);
  const [name, setName] = useState("");
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const [busy, setBusy] = useState(false);
  if (!state) throw new Error("ArchivedAppsProvider is required");
  const { apps, onRestored } = state;

  function open(app: ArchivedApp) {
    setRestoring(app);
    setName(app.name);
    setNotice(QUIET);
  }

  async function restore(event: FormEvent) {
    event.preventDefault();
    if (!restoring || !mainAgent || busy) return;
    setBusy(true);
    const outcome = await postIntent(mainAgent.id, {
      verb: "restore_application",
      app_id: restoring.id,
      name,
    });
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
      <Section title="Archived">
        <DataTable
          columns={["Application", { label: "Archived", fact: true }, { label: "", fact: true }]}
          rows={apps}
          rowKey={(app) => app.id}
          empty="No apps are archived."
        >
          {(app) => (
            <>
              <Td>
                <Lede mark={<AgentIcon name={app.icon} />}>
                  {agentName(app.name)}
                </Lede>
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
      </Section>
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
