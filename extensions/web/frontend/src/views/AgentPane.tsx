import { useBeside } from "@/kernel/beside";
import { BANDS, RecordPanel } from "@/kernel/pane";
import { usePlaceRecorder } from "@/kernel/place";
import { TabPanel, TabRow } from "@/kernel/tabs";
import { Radar } from "@/views/Radar";
import { AgentConnectors } from "@/views/Connectors";
import { Conversations } from "@/views/Conversations";
import { AgentSkills } from "@/views/AgentSkills";
import { Settings } from "@/views/Settings";
import { AgentUsage } from "@/views/Usage";
import type { AgentTab, PlaceStep, WorkspacePlace } from "@/lib/route";
import type { Agent } from "@/lib/types";

const TAB_LABELS: Record<AgentTab, string> = {
  settings: "Settings",
  conversations: "Conversations",
  radar: "Radar",
  connectors: "Connectors",
  skills: "Skills",
  usage: "Usage",
};

export type AgentPaneProps = {
  agent: Agent;
  tab: AgentTab;
  tabs: readonly AgentTab[];
  onTab: (tab: AgentTab) => void;
  onClose: () => void;
  place: WorkspacePlace;
  onPlace: (place: WorkspacePlace, step: PlaceStep) => void;
};

/** An agent is read beside the list it was opened from, not instead of it: the drawer states the
 *  one agent while the index behind it still states which agents there are, so closing it is a
 *  press rather than a way back. */
export function AgentPane({ agent, tab, tabs, onTab, onClose, place, onPlace }: AgentPaneProps) {
  const { key, merged, record } = usePlaceRecorder({
    view: tab,
    place,
    remountOnPlace: tab === "conversations",
    onPlace,
  });
  return useBeside(
    <RecordPanel onClose={onClose} title={agent.name}>
      <TabRow
        group="agent"
        tabs={tabs}
        current={tab}
        label={(name) => TAB_LABELS[name]}
        onPick={onTab}
      />
      <TabPanel group="agent" current={tab} className={BANDS} data-testid="panel">
        {tab === "settings" ? <Settings key={agent.id} agent={agent} /> : null}
        {tab === "radar" ? (
          <Radar key={agent.id} agentId={agent.id} place={merged} onPlace={record} />
        ) : null}
        {tab === "conversations" ? (
          <Conversations key={key} agent={agent} place={merged} onPlace={record} />
        ) : null}
        {tab === "connectors" ? <AgentConnectors agent={agent} /> : null}
        {tab === "skills" ? <AgentSkills agent={agent} /> : null}
        {tab === "usage" ? <AgentUsage agent={agent} /> : null}
      </TabPanel>
    </RecordPanel>,
  );
}
