import { IconSettings } from "@tabler/icons-react";

import { Button } from "@/components/ui/button";
import { usePlaceRecorder } from "@/kernel/place";
import { TabPanel, TabRow } from "@/kernel/tabs";
import { agentName } from "@/lib/agentName";
import { ConversationsPane } from "@/views/Conversations";
import { Homepage } from "@/views/Homepage";
import { AGENT_TAB_LABELS } from "@/views/registry";
import type { AgentTab, PlaceStep, WorkspacePlace } from "@/lib/route";
import type { Agent } from "@/lib/types";


export type AgentPaneProps = {
  agent: Agent;
  tab: AgentTab;
  tabs: readonly AgentTab[];
  onTab: (tab: AgentTab) => void;
  onNewChat: () => void;
  onSettings: () => void;
  place: WorkspacePlace;
  onPlace: (place: WorkspacePlace, step: PlaceStep) => void;
};

/** The wide pane of the agents screen: one header — the agent's name beside the tab strip — worn
 *  the way the chat pane wears its own, a full-width band whose rule separates it from the body,
 *  the body starting at the band pitch a record's groups are read at. No tab draws a header of its
 *  own.
 *
 *  Starting a conversation is not a tab — it leaves the page for the chat instead of swapping the
 *  panel under them, and a segment that can never read as the current one is not a segment. So it
 *  stands with the acts at the far end, in the filled ink the New application act is drawn in,
 *  which is what tells an act apart from the pills it would otherwise sit among. */
export function AgentPane({
  agent,
  tab,
  tabs,
  onTab,
  onNewChat,
  onSettings,
  place,
  onPlace,
}: AgentPaneProps) {
  const { key, merged, record } = usePlaceRecorder({
    view: tab,
    place,
    remountOnPlace: tab === "conversations",
    onPlace,
  });
  return (
    <section aria-label={agentName(agent.name)} className="flex min-h-0 min-w-0 flex-col">
      <header className="flex h-(--size-control) shrink-0 items-center gap-2xl border-b border-edge px-2xl py-lg box-content">
        <h2 className="m-0 min-w-0 truncate text-subtitle font-medium">{agentName(agent.name)}</h2>
        <TabRow
          group="agent"
          tabs={tabs}
          current={tab}
          label={(name) => AGENT_TAB_LABELS[name]}
          onPick={onTab}
        />
        <Button variant="send" size="bar" className="ml-auto shrink-0" onClick={onNewChat}>
          New chat
        </Button>
        <Button
          size="icon"
          aria-label={"Settings for " + agentName(agent.name)}
          className="shrink-0 rounded-full"
          onClick={onSettings}
        >
          <IconSettings className="size-icon" aria-hidden />
        </Button>
      </header>
      <TabPanel
        group="agent"
        current={tab}
        className="flex min-h-0 flex-1 flex-col"
        data-testid="panel"
      >
        {tab === "home" ? <Homepage key={agent.id} agent={agent} /> : null}
        {tab === "conversations" ? (
          <ConversationsPane key={key} agent={agent} place={merged} onPlace={record} />
        ) : null}
      </TabPanel>
    </section>
  );
}
