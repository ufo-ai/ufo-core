import { useEffect, useState, type ReactNode } from "react";
import { IconChevronDown } from "@tabler/icons-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Sheet } from "@/components/ui/sheet";
import { ObjectPane } from "@/kernel/objects";
import { Empty } from "@/kernel/panel";
import { agentName } from "@/lib/agentName";
import { chatState, clearChat, updateChat, useChat } from "@/lib/chatStore";
import { cn } from "@/lib/cn";
import { useMainAgent } from "@/lib/mainAgent";
import { openAgents, openHome } from "@/lib/router";
import { useSurfaces } from "@/lib/surfaces";
import {
  AgentPane,
  SETTINGS_TABS,
  SETTINGS_TAB_LABELS,
  SettingsTabItems,
  type SettingsTab,
} from "@/views/AgentPane";
import { AppBuilder } from "@/views/AppBuilder";
import { wizardKey } from "@/lib/wizard";
import { AgentConnectors } from "@/views/Connectors";
import { Settings } from "@/views/Settings";
import type { PlaceStep, WorkspacePlace } from "@/lib/route";
import type { ChatRow } from "@/lib/rail";
import type { Agent, Member } from "@/lib/types";

export type AgentsProps = {
  member: Member;
  selected: Agent | null;
  build: boolean;
  chats: ChatRow[] | null;
  place: WorkspacePlace;
  onPlace: (place: WorkspacePlace, step: PlaceStep) => void;
  onCreated: (agent: Agent, conversationId: string, title: string) => void;
  onAgents: () => void;
  onExitBuilder: () => void;
  onForwardAgents: () => void;
  buildWanted: boolean;
};

/** Work outranks the rest: an app that is running is telling the member something is happening now,
 *  and that is true whether or not its setup is finished. */
const TASK_KIND = "scheduled_task";

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

export function Agents({
  member,
  selected,
  build,
  chats,
  place,
  onPlace,
  onCreated,
  onAgents,
  onExitBuilder,
  onForwardAgents,
  buildWanted,
}: AgentsProps) {
  const mainAgent = useMainAgent();
  const surfaces = useSurfaces();
  const shown = selected ?? mainAgent;
  const key = mainAgent ? wizardKey(mainAgent.id) : null;
  const held = useChat(key ?? "");
  const running =
    key !== null &&
    !held.closed &&
    (held.busy || (held.messages ?? []).length > 0 || held.founded !== null);
  const building = mainAgent !== null && build && (buildWanted || running);
  const forwarding = build && !building;
  useEffect(() => {
    if (forwarding) onForwardAgents();
  }, [forwarding, onForwardAgents]);
  const [settling, setSettling] = useState(false);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>(SETTINGS_TABS[0]);
  const [scheduled, setScheduled] = useState<string[]>([]);

  if (forwarding) return null;

  return (
    <div className="relative grid min-h-0 min-w-0 flex-1 grid-cols-1">
      {building && mainAgent ? (
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
      ) : shown ? (
        <AgentPane
          key={shown.id}
          agent={shown}
          member={member}
          chats={chats}
          onCreated={(conversationId, title) => onCreated(shown, conversationId, title)}
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
      ) : (
        <Empty>No app is visible to you.</Empty>
      )}
      {shown && !building ? (
        <AppSettings
          agent={shown}
          tab={settingsTab}
          open={settling}
          onTab={setSettingsTab}
          onClose={() => setSettling(false)}
        >
          {settingsTab === "settings" ? (
            <Settings
              key={shown.id}
              agent={shown}
              onArchived={() => {
                setSettling(false);
                if (surfaces.apps) openAgents();
                else openHome();
                onAgents();
              }}
            />
          ) : null}
          {settingsTab === "connectors" ? <AgentConnectors agent={shown} /> : null}
          {settingsTab === "scheduled" ? (
            <ObjectPane
              key={shown.id}
              agentId={shown.id}
              kind={TASK_KIND}
              makes={false}
              opens={scheduled}
              onPlace={(next) => setScheduled(next.opens ?? [])}
            />
          ) : null}
        </AppSettings>
      ) : null}
    </div>
  );
}
