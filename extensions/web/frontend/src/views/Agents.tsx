import { useEffect, useState, type ReactNode } from "react";
import { IconChevronDown } from "@tabler/icons-react";

import { Sheet } from "@/components/ui/sheet";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ObjectPane } from "@/kernel/objects";
import { agentName } from "@/lib/agentName";
import { chatState, clearChat, updateChat, useChat } from "@/lib/chatStore";
import { cn } from "@/lib/cn";
import { useMainAgent } from "@/lib/mainAgent";
import { openApps } from "@/lib/router";
import { wizardKey } from "@/lib/wizard";
import {
  AgentPane,
  SETTINGS_TABS,
  SETTINGS_TAB_LABELS,
  SettingsTabItems,
  type SettingsTab,
} from "@/views/AgentPane";
import { AppBuilder } from "@/views/AppBuilder";
import { AgentConnectors } from "@/views/Connectors";
import { Settings } from "@/views/Settings";
import type { PlaceStep, WorkspacePlace } from "@/lib/route";
import type { ChatRow } from "@/lib/rail";
import type { Agent, Member } from "@/lib/types";

export type AgentsProps = {
  member: Member;
  selected: Agent;
  chats: ChatRow[] | null;
  place: WorkspacePlace;
  onPlace: (place: WorkspacePlace, step: PlaceStep) => void;
  onCreated: (agent: Agent, conversationId: string, title: string) => void;
  onAgents: () => void;
};

export type AgentBuilderProps = {
  member: Member;
  onAgents: () => void;
  onExitBuilder: () => void;
  onForwardApps: () => void;
  buildWanted: boolean;
};

const TASK_KIND = "scheduled_task";

/** It stays out of the modal state radix would take: a record opened from the tasks read raises the
 *  shared sheet over this panel, and a modal layer under it would hold the pointer away from it. */
export function AppSettings({
  agent,
  tab,
  open,
  onTab,
  onClose,
  children,
}: {
  agent: Agent;
  tab: SettingsTab;
  open: boolean;
  onTab: (tab: SettingsTab) => void;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <Sheet
      open={open}
      title={agentName(agent.name)}
      onClose={onClose}
      actions={
        <DropdownMenu modal={false}>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className={cn(
                "flex min-w-0 cursor-pointer items-center gap-xs border-0 bg-transparent p-0",
                "text-label text-ink-soft transition-colors duration-100 ease-control hover:text-ink",
              )}
            >
              <span className="min-w-0 truncate">{SETTINGS_TAB_LABELS[tab]}</span>
              <IconChevronDown className="size-(--size-glyph) shrink-0" aria-hidden />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-(--container-menu)">
            <SettingsTabItems onPick={onTab} />
          </DropdownMenuContent>
        </DropdownMenu>
      }
    >
      {children}
    </Sheet>
  );
}

export function AgentBuilder({
  member,
  onAgents,
  onExitBuilder,
  onForwardApps,
  buildWanted,
}: AgentBuilderProps) {
  const mainAgent = useMainAgent();
  const key = mainAgent ? wizardKey(mainAgent.id) : null;
  const held = useChat(key ?? "");
  const running =
    key !== null &&
    !held.closed &&
    (held.busy || (held.messages ?? []).length > 0 || held.founded !== null);
  const building = mainAgent !== null && (buildWanted || running);
  const forwarding = !building;
  useEffect(() => {
    if (forwarding) onForwardApps();
  }, [forwarding, onForwardApps]);

  if (!building || !mainAgent) return null;

  return (
    <div className="relative grid min-h-0 min-w-0 flex-1 grid-cols-1">
      <AppBuilder
        agent={mainAgent}
        member={member}
        onSettled={onAgents}
        onClose={() => {
          if (key !== null) {
            if (chatState(key).busy) updateChat(key, (state) => ({ ...state, closed: true }));
            else clearChat(key);
          }
          onExitBuilder();
        }}
      />
    </div>
  );
}

export function Agents({
  member,
  selected,
  chats,
  place,
  onPlace,
  onCreated,
  onAgents,
}: AgentsProps) {
  const [settling, setSettling] = useState(false);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>(SETTINGS_TABS[0]);
  const [scheduled, setScheduled] = useState<string[]>([]);

  return (
    <div className="relative grid min-h-0 min-w-0 flex-1 grid-cols-1">
      <AgentPane
        key={selected.id}
        agent={selected}
        member={member}
        chats={chats}
        onCreated={(conversationId, title) => onCreated(selected, conversationId, title)}
        onFounded={onCreated}
        onAgents={onAgents}
        onSettings={(tab) => {
          setSettingsTab(tab);
          setScheduled([]);
          setSettling(true);
        }}
        place={place}
        onPlace={onPlace}
      />
      <AppSettings
        agent={selected}
        tab={settingsTab}
        open={settling}
        onTab={setSettingsTab}
        onClose={() => setSettling(false)}
      >
        {settingsTab === "settings" ? (
          <Settings
            key={selected.id}
            agent={selected}
            onArchived={() => {
              setSettling(false);
              openApps();
              onAgents();
            }}
          />
        ) : null}
        {settingsTab === "connectors" ? <AgentConnectors agent={selected} /> : null}
        {settingsTab === "scheduled" ? (
          <ObjectPane
            key={selected.id}
            agentId={selected.id}
            kind={TASK_KIND}
            makes={false}
            opens={scheduled}
            onPlace={(next) => setScheduled(next.opens ?? [])}
          />
        ) : null}
      </AppSettings>
    </div>
  );
}
