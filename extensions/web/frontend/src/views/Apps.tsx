import { createContext, useContext, useState, type ReactNode } from "react";

import { IconSettings } from "@tabler/icons-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Filter } from "@/components/ui/filter";
import { ACTS, Lede, Td, TdActs, TdFact } from "@/components/ui/table";
import { ActionForm } from "@/kernel/action";
import { ObjectPane } from "@/kernel/objects";
import type { Placement } from "@/kernel/pager";
import { Panel, Section, usePanelRead } from "@/kernel/panel";
import { DataTable } from "@/kernel/table";
import { AgentIcon } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";
import { postAction } from "@/lib/api";
import { useMainAgent } from "@/lib/mainAgent";
import { Moment } from "@/lib/moments";
import { openAgent } from "@/lib/router";
import type { ActionView, Agent, ArchivedApp } from "@/lib/types";
import { AppSettings } from "@/views/Agents";
import { SETTINGS_TABS, type SettingsTab } from "@/views/AgentPane";
import { AgentConnectors } from "@/views/Connectors";
import { Settings } from "@/views/Settings";

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
  const [restoring, setRestoring] = useState<ArchivedApp | null>(null);

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
                <Button variant="row" onClick={() => setRestoring(app)}>
                  Restore
                </Button>
              </div>
            </TdActs>
          </>
        )}
      </DataTable>
      {restoring ? (
        <RestoreDialog
          key={restoring.id}
          app={restoring}
          onClose={() => setRestoring(null)}
          onRestored={() => {
            setRestoring(null);
            onRestored();
          }}
        />
      ) : null}
    </>
  );
}

/** The restore as the archived agent object projects it: the row's acts are read by its durable
 *  name — the restore, drawn from its own schema — and posted on the main agent's lane. The name the
 *  app had is seeded, since it is the one a member most often wants back. */
function RestoreDialog({
  app,
  onClose,
  onRestored,
}: {
  app: ArchivedApp;
  onClose: () => void;
  onRestored: () => void;
}) {
  const mainAgent = useMainAgent();
  const acts = usePanelRead<{ actions: ActionView[] }>(
    "/actions/agent/" + encodeURIComponent(app.object),
  );

  return (
    <Dialog open onOpenChange={(shown) => (shown ? null : onClose())}>
      <DialogContent className="w-settings" aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>Restore {agentName(app.name)}</DialogTitle>
        </DialogHeader>
        <Panel state={acts} shape="form">
          {({ actions }) =>
            actions.map((view) => (
              <ActionForm
                key={view.name}
                view={view}
                initial={{ new_name: app.name }}
                act={async (input) => {
                  if (!mainAgent) return { applied: false, message: "" };
                  const outcome = await postAction(mainAgent.id, view.call, input);
                  if (outcome.applied) onRestored();
                  return outcome;
                }}
              />
            ))
          }
        </Panel>
      </DialogContent>
    </Dialog>
  );
}
