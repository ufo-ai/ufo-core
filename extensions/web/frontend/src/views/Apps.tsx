import { useState, type FormEvent } from "react";

import { IconSettings } from "@tabler/icons-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/field";
import { Filter } from "@/components/ui/filter";
import { ACTS, Lede, Td, TdActs, TdFact } from "@/components/ui/table";
import { ObjectPane } from "@/kernel/objects";
import type { Placement } from "@/kernel/pager";
import { usePageAct } from "@/kernel/pane";
import { OutcomeNotice, QUIET, outcomeNotice, Section, type NoticeState } from "@/kernel/panel";
import { DataTable } from "@/kernel/table";
import { AgentIcon } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";
import { postObjectAction } from "@/lib/api";
import { useApps } from "@/lib/apps";
import { useMainAgent } from "@/lib/mainAgent";
import { Moment } from "@/lib/moments";
import { openAgent, openBuilder, openStore } from "@/lib/router";
import { useSurfaces } from "@/lib/surfaces";
import { APP_STORE_TITLE } from "@/lib/title";
import type { Agent, ArchivedApp } from "@/lib/types";
import { APP_CREATOR_TITLE } from "@/lib/wizard";
import { AppSettings } from "@/views/Agents";
import { SETTINGS_TABS, type SettingsTab } from "@/views/AgentPane";
import { AgentConnectors } from "@/views/Connectors";
import { Settings } from "@/views/Settings";

const CREATED_BY_ME = "Created by me";
const ARCHIVED = "Archived";
const FILTERS = [
  { label: CREATED_BY_ME, value: CREATED_BY_ME },
  { label: ARCHIVED, value: ARCHIVED },
];

export function Apps({
  place,
  onPlace,
}: {
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const offered = useSurfaces();
  const mainAgent = useMainAgent();
  const act = usePageAct(
    offered["app-store"] ? (
      <Button variant="send" size="bar" onClick={openStore}>
        {APP_STORE_TITLE}
      </Button>
    ) : mainAgent ? (
      <Button variant="send" size="bar" onClick={openBuilder}>
        {APP_CREATOR_TITLE}
      </Button>
    ) : null,
  );
  return (
    <>
      {act}
      <AppsTable place={place} onPlace={onPlace} />
    </>
  );
}

function AppsTable({
  place,
  onPlace,
}: {
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const { agents, archived, onRestored } = useApps();
  const [settingsApp, setSettingsApp] = useState<Agent | null>(null);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>(SETTINGS_TABS[0]);
  const [scheduled, setScheduled] = useState<string[]>([]);
  const picked = place.chip ?? "";
  const bar = (
    <Filter
      options={FILTERS}
      value={picked}
      onChange={(value) => onPlace({ chip: value || undefined })}
    />
  );
  const apps = picked === CREATED_BY_ME ? agents.filter((agent) => agent.mine) : agents;
  return (
    <>
      <Section bar={bar}>
        {picked === ARCHIVED ? (
          <ArchivedTable apps={archived} onRestored={onRestored} />
        ) : (
          <DataTable
            columns={["Application", "Purpose", { label: "", fact: true }]}
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
                <TdActs>
                  <div className={ACTS}>
                    <Button
                      variant="quiet"
                      size="icon"
                      aria-label={"Settings for " + agentName(app.name)}
                      onClick={() => {
                        setSettingsTab(SETTINGS_TABS[0]);
                        setScheduled([]);
                        setSettingsApp(app);
                      }}
                    >
                      <IconSettings aria-hidden />
                    </Button>
                  </div>
                </TdActs>
              </>
            )}
          </DataTable>
        )}
      </Section>
      {settingsApp ? (
        <AppSettings
          agent={settingsApp}
          tab={settingsTab}
          open
          onTab={setSettingsTab}
          onClose={() => setSettingsApp(null)}
        >
          {settingsTab === "settings" ? (
            <Settings
              agent={settingsApp}
              onArchived={() => {
                setSettingsApp(null);
                onRestored();
              }}
            />
          ) : null}
          {settingsTab === "connectors" ? <AgentConnectors agent={settingsApp} /> : null}
          {settingsTab === "scheduled" ? (
            <ObjectPane
              agentId={settingsApp.id}
              kind="scheduled_task"
              makes={false}
              opens={scheduled}
              onPlace={(next) => setScheduled(next.opens ?? [])}
            />
          ) : null}
        </AppSettings>
      ) : null}
    </>
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
          <form aria-label="Restore" onSubmit={restore} className="flex flex-col gap-xl">
            <Field label="Name" htmlFor="restore-name">
              <Input
                id="restore-name"
                required
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </Field>
            <OutcomeNotice state={notice} />
            <div className="flex items-baseline justify-end gap-lg">
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
