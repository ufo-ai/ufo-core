import { useEffect, useState, type ReactNode } from "react";
import {
  IconApps,
  IconChevronDown,
  IconCirclePlus,
  IconDots,
  IconPin,
  IconPinFilled,
} from "@tabler/icons-react";

import {
  SIDEBAR_FOLDED,
  SIDEBAR_PRESS,
  SIDEBAR_ROW,
  SidebarPress,
  SidebarRow,
} from "@/components/Sidebar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Sheet } from "@/components/ui/sheet";
import { Ticker } from "@/components/ui/ticker";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ObjectPane } from "@/kernel/objects";
import { Empty } from "@/kernel/panel";
import { AgentIcon } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";
import { useAppStatus, type AgentStatus } from "@/lib/appStatusStore";
import { chatState, clearChat, updateChat, useChat } from "@/lib/chatStore";
import { cn } from "@/lib/cn";
import { useMainAgent } from "@/lib/mainAgent";
import { openAgents } from "@/lib/router";
import { useSurfaces } from "@/lib/surfaces";
import {
  AgentPane,
  SETTINGS_TABS,
  SETTINGS_TAB_LABELS,
  SettingsTabItems,
  type SettingsTab,
} from "@/views/AgentPane";
import { APP_CREATOR_TITLE, AppBuilder, wizardKey } from "@/views/AppBuilder";
import { AgentConnectors } from "@/views/Connectors";
import { Settings } from "@/views/Settings";
import { APP_STORE_TITLE } from "@/views/Store";
import type { PlaceStep, WorkspacePlace } from "@/lib/route";
import { appOrder, appRun, type ChatRow } from "@/lib/rail";
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
function statusDot(status: AgentStatus | undefined, setupDue: boolean): string | null {
  if (status?.turn === "running" || status?.turn === "queued") return "bg-live";
  if (setupDue || status?.turn === "parked" || status?.last_failed) return "bg-blocked";
  return null;
}

function AgentRow({
  agent,
  status,
  open,
  pinned,
  collapsed,
  onPin,
  onOpen,
}: {
  agent: Agent;
  status: AgentStatus | undefined;
  open: boolean;
  pinned: boolean;
  collapsed: boolean;
  onPin: () => void;
  onOpen: () => void;
}) {
  const [asks, setAsks] = useState(0);
  const dot = statusDot(status, agent.setup_due === true);
  const row = (
    <SidebarRow current={open}>
      <SidebarPress
        current={open}
        collapsed={collapsed}
        label={agentName(agent.name)}
        className="py-2xs"
        onClick={onOpen}
        onPointerEnter={() => setAsks((asked) => asked + 1)}
        onPointerLeave={() => setAsks(0)}
        onFocus={() => setAsks((asked) => asked + 1)}
        onBlur={() => setAsks(0)}
        glyph={
          <span className="relative shrink-0">
            <AgentIcon name={agent.icon} className="size-(--size-glyph)" />
            <span
              aria-hidden
              className={cn(
                "absolute -right-2xs -bottom-2xs size-sm rounded-full transition duration-200 ease-control",
                dot ?? "scale-0",
              )}
            />
          </span>
        }
      >
        <Ticker asks={asks} className="flex-1 text-label">
          {agentName(agent.name)}
        </Ticker>
      </SidebarPress>
      {collapsed ? null : (
        <button
          type="button"
          aria-label={(pinned ? "Unpin " : "Pin ") + agentName(agent.name)}
          aria-pressed={pinned}
          onClick={onPin}
          className={cn(
            "mr-xs shrink-0 rounded-control border-0 bg-transparent p-2xs text-ink-soft hover:bg-fill",
            "opacity-0 group-hover/row:opacity-100 focus-visible:opacity-100",
          )}
        >
          {pinned ? (
            <IconPinFilled className="size-icon" aria-hidden />
          ) : (
            <IconPin className="size-icon" aria-hidden />
          )}
        </button>
      )}
    </SidebarRow>
  );
  if (!collapsed) return row;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{row}</TooltipTrigger>
      <TooltipContent side="right">{agentName(agent.name)}</TooltipContent>
    </Tooltip>
  );
}

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

export function AppsIndex({
  agents,
  openId,
  store,
  pinned,
  expanded,
  collapsed,
  onExpand,
  onPin,
  onOpen,
  onStore,
  onBuild,
}: {
  agents: Agent[];
  openId: string | null;
  store: boolean;
  pinned: string[];
  expanded: boolean;
  collapsed: boolean;
  onExpand: (expanded: boolean) => void;
  onPin: (agentId: string) => void;
  onOpen: (agentId: string) => void;
  onStore: () => void;
  onBuild: () => void;
}) {
  const mainAgent = useMainAgent();
  const offered = useSurfaces();
  const { statuses } = useAppStatus();
  /** Work in flight lifts a row to the top rather than moving it through this ladder: an app that sorted
   *  as `now` while it worked would drop back down the column the moment it stopped. */
  const lastActiveAt = (agentId: string): number | null => {
    const at = statuses[agentId]?.last_active_at;
    return at ? Date.parse(at) : null;
  };
  const working = (agentId: string): boolean => {
    const status = statuses[agentId];
    return status?.turn === "running" || status?.turn === "queued";
  };
  const ordered = appOrder(agents, pinned, lastActiveAt);
  const run = appRun(ordered, working);
  const shown = expanded ? run.shown.concat(run.more) : run.shown;
  return (
    <nav aria-label="Apps" className="flex min-h-0 flex-col">
      <div className="flex min-h-0 max-h-(--size-apps-open) flex-col overflow-y-auto">
        <ul className="m-0 flex list-none flex-col gap-px p-0">
          {shown.map((agent) => (
            <AgentRow
              key={agent.id}
              agent={agent}
              status={statuses[agent.id]}
              open={agent.id === openId}
              pinned={pinned.includes(agent.id)}
              collapsed={collapsed}
              onPin={() => onPin(agent.id)}
              onOpen={() => onOpen(agent.id)}
            />
          ))}
          {offered["app-store"] ? (
            <SidebarRow current={store}>
              <SidebarPress
                current={store}
                collapsed={collapsed}
                label={APP_STORE_TITLE}
                glyph={<IconApps className="size-(--size-glyph) shrink-0" aria-hidden />}
                onClick={onStore}
              />
            </SidebarRow>
          ) : mainAgent ? (
            <SidebarRow>
              <SidebarPress
                collapsed={collapsed}
                label={APP_CREATOR_TITLE}
                glyph={<IconCirclePlus className="size-(--size-glyph) shrink-0" aria-hidden />}
                onClick={onBuild}
              />
            </SidebarRow>
          ) : null}
        </ul>
      </div>
      {run.more.length || expanded ? (
        <button
          type="button"
          aria-expanded={expanded}
          aria-label={expanded ? "Less applications" : "More applications"}
          onClick={() => onExpand(!expanded)}
          className={cn(
            SIDEBAR_ROW,
            SIDEBAR_PRESS,
            "shrink-0 text-ink-soft",
            collapsed && SIDEBAR_FOLDED,
          )}
        >
          <IconDots className="size-(--size-glyph) shrink-0" aria-hidden />
          <span className={cn("min-w-0 flex-1 truncate", collapsed && "hidden")}>
            {expanded ? "Less" : "More"}
          </span>
        </button>
      ) : null}
    </nav>
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
                openAgents();
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
