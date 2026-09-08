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
  /** The agent the hash names, or null on the bare route — which shows the main agent without
   *  navigating. */
  selected: Agent | null;
  /** Whether the hash names the wizard: the builder holds the pane whatever run state it finds. */
  build: boolean;
  chats: ChatRow[] | null;
  place: WorkspacePlace;
  onPlace: (place: WorkspacePlace, step: PlaceStep) => void;
  onCreated: (agent: Agent, conversationId: string, title: string) => void;
  onAgents: () => void;
  /** Leaves the wizard's address for the apps screen, which closing the builder is. */
  onExitBuilder: () => void;
  /** Where an unbacked wizard address forwards, replacing itself rather than stacking. */
  onForwardAgents: () => void;
  /** Whether a member's press raised the wizard: the address alone must not found a run. */
  buildWanted: boolean;
};

/** The dot the mark wears, read before any words: the live tone while the app holds work in
 *  flight, and the blocked tone while it is waiting on the member.
 *
 *  An app that is paused, whose last run failed, or that was installed and never set up are one
 *  state to a member scanning a column — none of them is going to do anything until they act. They
 *  differ in what to do next, which is the row's own screen to say, not a second colour's.
 *
 *  Work outranks the rest: an app that is running is telling the member something is happening now,
 *  and that is true whether or not its setup is finished. */
function statusDot(status: AgentStatus | undefined, setupDue: boolean): string | null {
  if (status?.turn === "running" || status?.turn === "queued") return "bg-live";
  if (setupDue || status?.turn === "parked" || status?.last_failed) return "bg-blocked";
  return null;
}

/** One app's row: the whole row is the one control, and it opens the app. What acts on the open app
 *  is worn by that app's own pane, beside its name.
 *
 *  The row is its name and its dot, one line whatever the app is doing. What the app is doing is
 *  the dot's to say, which costs the column no height; the words for it are the app's own pane's,
 *  where there is room to read them. The name is held to the column and states its tail by
 *  travelling while the member is on the row, the way a conversation's title does. */
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
  /** Whether the sidebar stands folded to its glyph rail, where the row is its mark and its dot. */
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
          /* The mark is the glyph a sidebar row draws, bare in the row's own ink, so the list reads
             as the sidebar the pin act puts a row into. */
          <span className="relative shrink-0">
            <AgentIcon name={agent.icon} className="size-(--size-glyph)" />
            {/* The dot is always drawn and scales away when the app has nothing to say, so a change
                of state is a mark growing or turning rather than one appearing out of nothing. */}
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
      {/* Pinning moves the app up the sidebar's order. The act rests until the pointer is on the
          row whether or not it is already done: a mark standing on every pinned row is a column of
          controls nobody is using, and where the pinned rows lead the column that is most of it.
          What the pin did is read off the order, which is the thing it changed. */}
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
  /* On the glyph rail a row is its mark, so the name it cannot draw is held at the pointer — the
     same bargain every other folded row in the sidebar makes. */
  return (
    <Tooltip>
      <TooltipTrigger asChild>{row}</TooltipTrigger>
      <TooltipContent side="right">{agentName(agent.name)}</TooltipContent>
    </Tooltip>
  );
}

/** The clock-fired tasks the app holds. Radar reads them across the workspace, beside what they
 *  did; here they are read for the one app they run on. A member writes one on the workspace's own
 *  tasks screen, which is the screen that asks which app runs it — this panel names one app already,
 *  and a second place to write a task is a second place that has to state the same rules. */
const TASK_KIND = "scheduled_task";

/** An app's three reads, standing in the shared drawer every record on the screen opens in: one
 *  width, one band, one way out, so a member reads an app's settings where they read everything
 *  else. The band names the app, and the read showing stands beside it under a chevron that reaches
 *  the other two — a member switches reads without going back to the band they came from. */
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

/** The apps index: the workspace's apps as the sidebar draws them, and as the last row the place
 *  that adds to them — the store where the deploy offers it, App Creator where it does not yet.
 *  What each app is doing comes from `appStatusStore`, which every reader of the rows shares.
 *
 *  The column is shared with the member's conversations, so the list states a run of itself and
 *  holds the rest behind the `More` row. It takes what the sidebar can spare and scrolls inside
 *  that, opened or not: a list that grew with the workspace would push the conversations off the
 *  screen entirely, and a member who pinned thirty apps did that to the column just as surely as
 *  one who opened the tail. The row that opens the tail stands outside the scroll, so the way to
 *  close the list again never scrolls away from the member who opened it. */
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
  /** The agent whose pane the page is showing, or null when no app holds it. */
  openId: string | null;
  /** Whether the store holds the pane, which draws its row as the place the member already is. */
  store: boolean;
  pinned: string[];
  /** Whether the member has opened the list past the run it draws on its own. */
  expanded: boolean;
  /** Whether the sidebar stands folded to its glyph rail. */
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
  /** Where an app stands in time — what it last did, and nothing about what it is doing now. Work
   *  in flight lifts a row to the top of the list rather than moving it through this ladder: an app
   *  that sorted as `now` while it worked would drop back down the column the moment it stopped,
   *  and take every row it passed with it. */
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
          {/* Adding an app is the one act this list carries, and it stands under the apps rather
              than over them: the column is read for the app a member is going to open, and the place
              that adds one is what they reach when none of those is it. Where the deploy offers the
              store, that place is the store — the deploy's apps to install, and the wizard that
              builds a new one. Where it does not yet, it is the wizard itself, which rides the main
              agent's own chat, so a workspace with no main agent offers nothing to ride. */}
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
      {/* The rest of the workspace's apps, behind one row. It states what it does rather than how
          many it holds: a count is read as a badge of things wanting attention, and these are
          only the apps nobody pinned. The row stands while there is a tail to open or a run to
          close, so the member who opened the list can put it back. */}
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

/** The apps screen: the selected app's pane whole — the main app on the bare route — or the
 *  app-building wizard at its own address; switching apps is the sidebar's own list. */
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
  // The wizard's address is honoured only behind a member's press or a run already in flight: the
  // builder's mount founds a conversation, and Back or a reload landing on the bare address must
  // not send a turn nobody asked for.
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
            // Closing ends the run's presence here. A run whose founding send is still in flight
            // cannot be cleared out from under that send, so the key wears the close instead and
            // the landing leaves no forwarding record behind. The wizard's own address closes by
            // navigation, back to the screen it was raised over.
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
