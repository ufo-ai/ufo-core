import { useState } from "react";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ObjectPane } from "@/kernel/objects";
import { BesideHost } from "@/kernel/beside";
import { BANDS } from "@/kernel/pane";
import { TabPanel, TabRow } from "@/kernel/tabs";
import { AgentIcon } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";
import { cn } from "@/lib/cn";
import { useMainAgent } from "@/lib/mainAgent";
import { AgentPane } from "@/views/AgentPane";
import { AgentSkills } from "@/views/AgentSkills";
import { APP_BUILDER_TITLE, AppBuilder } from "@/views/AppBuilder";
import { AgentConnectors } from "@/views/Connectors";
import { Settings } from "@/views/Settings";
import { AGENT_TABS, type AgentTab, type PlaceStep, type WorkspacePlace } from "@/lib/route";
import type { Agent, Member } from "@/lib/types";

export type AgentsProps = {
  agents: Agent[];
  member: Member;
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

const MAIN = "Main";

/** One app-building run, while its wizard holds the pane: what the chat route called the
 *  conversation the opening message founded, absent until that message lands. The run is nothing but
 *  this — client-side state a reload clears, so no phantom row survives one. */
type Run = { title: string | null };

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

/** The apps screen: a thin index — the New application act over one row per app, the open app's
 *  settings behind the gear beside it — next to a wide pane holding the selected app, or the
 *  app-building wizard while a run is open. On a narrow screen the index is the page and a
 *  hash-named app overlays it. */
export function Agents({
  agents,
  member,
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
  const [run, setRun] = useState<Run | null>(null);
  const [settling, setSettling] = useState(false);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>(SETTINGS_TABS[0]);

  return (
    <div className="relative grid min-h-0 min-w-0 flex-1 grid-cols-[var(--container-sidebar)_1fr] max-narrow:grid-cols-1">
      <div
        className={cn(
          "flex min-h-0 flex-col gap-2xl overflow-y-auto",
          "border-r border-edge bg-sidebar px-sm py-2xl max-narrow:border-r-0",
          /* The pane covers this index on a narrow screen, so the index stops answering while
             it is covered: a row behind the cover is still a tab stop and still a row a reader
             reads out, and neither states where the member actually is. */
          selected && "max-narrow:invisible",
        )}
      >
        <nav aria-label="Agents" className="flex shrink-0 flex-col gap-sm">
          {/* Every member is offered the act: the `agent` kind admits a create from any speaking
              member and stamps them the owner, and the wizard rides the main agent's own chat. */}
          {mainAgent ? (
            <Button
              variant="send"
              size="bar"
              className="shrink-0"
              onClick={() => setRun({ title: null })}
            >
              New application
            </Button>
          ) : null}
          <ul className="m-0 flex list-none flex-col gap-px p-0">
            {/* The run in flight, named the way the wizard's own pane is until the conversation has a
                title of its own. It is the pane the screen is showing, so the row states where the
                member already is rather than offering a place to go — and it is not an app: nothing
                here opens one, and the app's real row arrives from the apps read when it lands. */}
            {run ? (
              <li className="flex items-center gap-xs rounded-control bg-fill pr-xs">
                <div className="flex min-w-0 flex-1 flex-col gap-2xs px-sm py-xs">
                  <span className="min-w-0 truncate text-label">
                    {run.title ? APP_BUILDER_TITLE + ": " + run.title : APP_BUILDER_TITLE}
                  </span>
                  <span className="w-full truncate font-mono text-small text-ink-soft">Building</span>
                </div>
              </li>
            ) : null}
            {agents.map((agent) => {
              const open = !run && agent.id === shown?.id;
              return (
                /** The whole row is the one control: it opens the app, and nothing else stands on
                 *  it. What acts on the open app is worn by that app's own pane, beside its name. */
                <li
                  key={agent.id}
                  className={cn(
                    "flex items-center rounded-control hover:bg-fill",
                    open && "bg-fill",
                  )}
                >
                  <button
                    type="button"
                    aria-current={open}
                    onClick={() => {
                      setRun(null);
                      onOpen(agent.id);
                    }}
                    className={cn(
                      "flex min-w-0 flex-1 items-center gap-sm border-0 bg-transparent",
                      "px-sm py-xs text-left text-inherit",
                    )}
                  >
                    {/* Both facts are read in the row's own ink rather than the soft tone: soft ink
                        clears the contrast floor over the pane's surface and not over the fill an
                        open or hovered row draws, and these two words are the smallest text in the
                        rail. Size and the mono face carry the hierarchy instead. */}
                    <Avatar>
                      <AvatarFallback>
                        <AgentIcon name={agent.icon} />
                      </AvatarFallback>
                    </Avatar>
                    <span className="flex min-w-0 flex-1 flex-col gap-2xs">
                      <span className="flex w-full items-baseline gap-sm">
                        <span className="min-w-0 truncate text-label">{agentName(agent.name)}</span>
                        {agent.main ? <span className="text-small">{MAIN}</span> : null}
                      </span>
                      <span className="w-full truncate font-mono text-small">{agent.model}</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </nav>
      </div>
      {run && mainAgent ? (
        <AppBuilder
          agent={mainAgent}
          member={member}
          onOpened={(title) => setRun({ title })}
          onSettled={onAgents}
          onClose={() => setRun(null)}
        />
      ) : shown ? (
        <AgentPane
          agent={shown}
          tab={tab}
          tabs={AGENT_TABS}
          selected={selected !== null}
          onTab={onTab}
          onSettings={() => {
            setSettingsTab(SETTINGS_TABS[0]);
            setSettling(true);
          }}
          place={place}
          onPlace={onPlace}
        />
      ) : (
        <div className="m-auto max-w-empty text-center text-ink-soft max-narrow:hidden">
          No agent is visible to you.
        </div>
      )}
      {shown && !run ? (
        <Dialog open={settling} onOpenChange={setSettling}>
          <DialogContent className="w-settings" aria-describedby={undefined}>
            {/* A record the dialog raises — a connector's own row — has to stand inside it: the
                pane's column lies under the dialog's scrim, where nothing can reach it. */}
            <BesideHost over>
              <DialogHeader className="flex-row items-center gap-2xl">
                <DialogTitle className="min-w-0 flex-1 truncate">{agentName(shown.name)}</DialogTitle>
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
    </div>
  );
}
