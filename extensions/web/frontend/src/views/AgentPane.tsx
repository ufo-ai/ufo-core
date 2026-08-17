import { BANDS } from "@/kernel/pane";
import { usePlaceRecorder } from "@/kernel/place";
import { TabPanel, TabRow } from "@/kernel/tabs";
import { cn } from "@/lib/cn";
import { Radar } from "@/views/Radar";
import { Conversations } from "@/views/Conversations";
import { AgentSkills } from "@/views/AgentSkills";
import { Homepage } from "@/views/Homepage";
import { Settings } from "@/views/Settings";
import { AGENT_TAB_LABELS } from "@/views/registry";
import type { AgentTab, PlaceStep, WorkspacePlace } from "@/lib/route";
import type { Agent } from "@/lib/types";

export type AgentPaneProps = {
  agent: Agent;
  tab: AgentTab;
  tabs: readonly AgentTab[];
  /** Whether the hash names this agent. On a narrow screen the index is the page, so only a named
   *  agent covers it — the one the bare route merely defaults to stays behind the index. */
  selected: boolean;
  onTab: (tab: AgentTab) => void;
  place: WorkspacePlace;
  onPlace: (place: WorkspacePlace, step: PlaceStep) => void;
};

/** The wide pane of the agents screen: one header — the agent's name beside the tab strip — over
 *  whichever tab body is open, the body starting at the band pitch a record's groups are read at.
 *  No tab draws a header of its own. */
export function AgentPane({ agent, tab, tabs, selected, onTab, place, onPlace }: AgentPaneProps) {
  const { key, merged, record } = usePlaceRecorder({
    view: tab,
    place,
    remountOnPlace: tab === "conversations",
    onPlace,
  });
  return (
    <section
      aria-label={agent.name}
      className={cn(
        "flex min-h-0 min-w-0 flex-col gap-6xl",
        "px-(--size-page-gutter) py-(--size-page-top) max-narrow:px-2xl",
        selected
          ? "max-narrow:absolute max-narrow:inset-0 max-narrow:z-10 max-narrow:bg-surface"
          : "max-narrow:hidden",
      )}
    >
      <header className="flex h-(--size-control) shrink-0 items-center gap-2xl">
        <h2 className="m-0 min-w-0 truncate text-subtitle font-medium">{agent.name}</h2>
        <TabRow
          group="agent"
          tabs={tabs}
          current={tab}
          label={(name) => AGENT_TAB_LABELS[name]}
          onPick={onTab}
        />
      </header>
      <TabPanel
        group="agent"
        current={tab}
        className={cn(BANDS, "min-h-0 flex-1 overflow-y-auto scrollbar-gutter-stable")}
        data-testid="panel"
      >
        {tab === "home" ? <Homepage key={agent.id} agent={agent} /> : null}
        {tab === "settings" ? <Settings key={agent.id} agent={agent} /> : null}
        {tab === "radar" ? (
          <Radar key={agent.id} agentId={agent.id} place={merged} onPlace={record} />
        ) : null}
        {tab === "conversations" ? (
          <Conversations key={key} agent={agent} place={merged} onPlace={record} />
        ) : null}
        {tab === "skills" ? <AgentSkills agent={agent} /> : null}
      </TabPanel>
    </section>
  );
}
