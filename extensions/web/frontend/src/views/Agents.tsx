import { useState } from "react";
import { IconSettings } from "@tabler/icons-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { SILENT, Toast, type ToastState } from "@/components/ui/toast";
import { ObjectPane, SpecPanel, type ObjectValue, type SpecEnvelope } from "@/kernel/objects";
import { BesideHost, useBeside } from "@/kernel/beside";
import { BANDS } from "@/kernel/pane";
import { outcomeNotice, type NoticeState } from "@/kernel/panel";
import { TabPanel, TabRow } from "@/kernel/tabs";
import { postIntent } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useMainAgent } from "@/lib/mainAgent";
import { AgentPane } from "@/views/AgentPane";
import { AgentSkills } from "@/views/AgentSkills";
import { AgentConnectors } from "@/views/Connectors";
import { Settings } from "@/views/Settings";
import { AGENT_TABS, type AgentTab, type PlaceStep, type WorkspacePlace } from "@/lib/route";
import type { Agent, NewAgentForm } from "@/lib/types";

export type AgentsProps = {
  agents: Agent[];
  newAgent: NewAgentForm | null;
  /** The agent the hash names, or null on the bare route — which shows the main agent without
   *  navigating. */
  selected: Agent | null;
  tab: AgentTab;
  place: WorkspacePlace;
  onOpen: (agentId: string) => void;
  onTab: (tab: AgentTab) => void;
  onPlace: (place: WorkspacePlace, step: PlaceStep) => void;
  onAgents: () => void;
};

const AGENT_KIND = "agent";
const MODEL_FIELD = "model";
const MAIN = "Main";

/** The clock-fired tasks the app holds. Radar reads them across the workspace, beside what they
 *  did; here they are read and written for the one app they run on, which is where a member sets
 *  one up. */
const TASK_KIND = "scheduled_task";

/** What an app's own dialog holds: the spec the member edits, the accounts the app reaches, the
 *  tasks that run it on a clock, and the skills it carries. Four reads of one app, none of which
 *  heads a page of its own. */
const SETTINGS_TABS = ["settings", "connectors", "scheduled", "skills"] as const;
type SettingsTab = (typeof SETTINGS_TABS)[number];
const SETTINGS_TAB_LABELS: Record<SettingsTab, string> = {
  settings: "Settings",
  connectors: "Connectors",
  scheduled: "Scheduled",
  skills: "Skills",
};

/** The values a create form opens on: one for every field whose choices are closed — the deploy's
 *  model ids, and the enums the spec declares. A picker the member never opened would otherwise
 *  submit nothing and earn a refusal for a value the form was already showing. */
function initialSpec(form: NewAgentForm): Record<string, ObjectValue> {
  const properties = form.spec_schema.properties ?? {};
  return Object.fromEntries(
    Object.entries(properties).flatMap(([field, property]) => {
      const choices = field === MODEL_FIELD ? form.models : property.enum;
      return choices?.length ? [[field, choices[0]]] : [];
    }),
  );
}

/** The apps screen: a thin index — the New app act over one row per app, the open app's settings
 *  behind the gear beside it — next to a wide pane holding the selected app. On a narrow screen
 *  the index is the page and a hash-named app overlays it. */
export function Agents({
  agents,
  newAgent,
  selected,
  tab,
  place,
  onOpen,
  onTab,
  onPlace,
  onAgents,
}: AgentsProps) {
  const mainAgent = useMainAgent();
  const shown = selected ?? mainAgent;
  const [creating, setCreating] = useState(false);
  const [settling, setSettling] = useState(false);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>(SETTINGS_TABS[0]);
  const [toast, setToast] = useState<ToastState>(SILENT);

  /** The `agent` kind takes a create only from a workspace admin speaking on the main agent's
   *  lane, so that is the lane the intent rides. A create that landed is read back through the one
   *  answer to what agents exist, which the index, the sidebar, and the router all draw from. */
  async function create(lane: string, envelope: SpecEnvelope): Promise<NoticeState> {
    const outcome = await postIntent(lane, { ...envelope, create_only: true });
    if (outcome.applied) {
      onAgents();
      setToast({ title: "Created " + envelope.name + "." });
    }
    return outcomeNotice(outcome);
  }

  const beside = useBeside(
    creating && newAgent && mainAgent ? (
      <SpecPanel
        schema={newAgent.spec_schema}
        kind={AGENT_KIND}
        name={null}
        spec={initialSpec(newAgent)}
        title="New app"
        options={{ [MODEL_FIELD]: newAgent.models }}
        onDone={(envelope) => create(mainAgent.id, envelope)}
        onClose={() => setCreating(false)}
      />
    ) : null,
    () => setCreating(false),
  );

  return (
    <div className="relative grid min-h-0 min-w-0 flex-1 grid-cols-[var(--container-index)_1fr] max-narrow:grid-cols-1">
      <nav
        aria-label="Agents"
        className={cn(
          "flex min-h-0 flex-col gap-lg overflow-y-auto",
          "border-r border-edge px-2xl py-2xl max-narrow:border-r-0",
        )}
      >
        {newAgent && mainAgent ? (
          <Button variant="send" size="bar" className="shrink-0" onClick={() => setCreating(true)}>
            New app
          </Button>
        ) : null}
        <ul className="m-0 flex list-none flex-col gap-px p-0">
          {agents.map((agent) => {
            const open = agent.id === shown?.id;
            return (
              /** The gear stands on the open row rather than over the list: it acts on that one
               *  app, and a control that named none while the list named several would be read as
               *  reaching all of them. It is a sibling of the row, never inside it — the row is
               *  itself the control that opens the app. */
              <li
                key={agent.id}
                className={cn(
                  "flex items-center gap-xs rounded-control pr-xs hover:bg-fill",
                  open && "bg-fill",
                )}
              >
                <button
                  type="button"
                  aria-current={open}
                  onClick={() => onOpen(agent.id)}
                  className={cn(
                    "flex min-w-0 flex-1 flex-col gap-2xs border-0 bg-transparent",
                    "px-sm py-xs text-left text-inherit",
                  )}
                >
                  <span className="flex w-full items-baseline gap-sm">
                    <span className="min-w-0 truncate text-label">{agent.name}</span>
                    {agent.main ? <span className="text-small text-ink-soft">{MAIN}</span> : null}
                  </span>
                  <span className="w-full truncate font-mono text-small text-ink-soft">
                    {agent.model}
                  </span>
                </button>
                {open ? (
                  <Button
                    size="icon"
                    aria-label={"Settings for " + agent.name}
                    className="shrink-0 rounded-full"
                    onClick={() => {
                      setSettingsTab(SETTINGS_TABS[0]);
                      setSettling(true);
                    }}
                  >
                    <IconSettings className="size-icon" aria-hidden />
                  </Button>
                ) : null}
              </li>
            );
          })}
        </ul>
      </nav>
      {shown ? (
        <AgentPane
          agent={shown}
          tab={tab}
          tabs={AGENT_TABS}
          selected={selected !== null}
          onTab={onTab}
          place={place}
          onPlace={onPlace}
        />
      ) : (
        <div className="m-auto max-w-empty text-center text-ink-soft max-narrow:hidden">
          No agent is visible to you.
        </div>
      )}
      {shown ? (
        <Dialog open={settling} onOpenChange={setSettling}>
          <DialogContent className="w-settings" aria-describedby={undefined}>
            {/* A record the dialog raises — a connector's own row — has to stand inside it: the
                pane's column lies under the dialog's scrim, where nothing can reach it. */}
            <BesideHost over>
              <DialogHeader className="flex-row items-center gap-2xl">
                <DialogTitle className="min-w-0 flex-1 truncate">{shown.name}</DialogTitle>
                <TabRow
                  group="agent-settings"
                  tabs={SETTINGS_TABS}
                  current={settingsTab}
                  label={(name) => SETTINGS_TAB_LABELS[name]}
                  onPick={setSettingsTab}
                />
              </DialogHeader>
              <TabPanel group="agent-settings" current={settingsTab} className={BANDS}>
                {settingsTab === "settings" ? <Settings key={shown.id} agent={shown} /> : null}
                {settingsTab === "connectors" ? <AgentConnectors agent={shown} /> : null}
                {settingsTab === "scheduled" ? (
                  <ObjectPane key={shown.id} agentId={shown.id} kind={TASK_KIND} />
                ) : null}
                {settingsTab === "skills" ? <AgentSkills key={shown.id} agent={shown} /> : null}
              </TabPanel>
            </BesideHost>
          </DialogContent>
        </Dialog>
      ) : null}
      {beside}
      <Toast state={toast} onDone={() => setToast(SILENT)} />
    </div>
  );
}
