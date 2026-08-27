import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { IconChevronDown, IconPin, IconPinFilled, IconX } from "@tabler/icons-react";

import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ObjectPane } from "@/kernel/objects";
import { Empty, usePanelRead } from "@/kernel/panel";
import { AgentIcon } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";
import { chatState, clearChat, updateChat, useChat } from "@/lib/chatStore";
import { cn } from "@/lib/cn";
import { useMainAgent } from "@/lib/mainAgent";
import { friendlyMoment } from "@/lib/moments";
import { openAgents } from "@/lib/router";
import {
  AgentPane,
  SETTINGS_TABS,
  SETTINGS_TAB_LABELS,
  SettingsTabItems,
  type SettingsTab,
} from "@/views/AgentPane";
import { APP_BUILDER_TITLE, AppBuilder, wizardKey } from "@/views/AppBuilder";
import { AgentConnectors } from "@/views/Connectors";
import { Settings } from "@/views/Settings";
import type { PlaceStep, WorkspacePlace } from "@/lib/route";
import type { ChatRow } from "@/lib/rail";
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

const MAIN = "Main";

/** One app's live picture: the liveest turn it holds, what that turn is doing when the engine
 *  said so, and the marks that place a resting app in time. */
export type AgentStatus = {
  agent_id: string;
  turn: "running" | "queued" | "parked" | null;
  activity: string | null;
  next_run_at: string | null;
  last_active_at: string | null;
  last_failed: boolean;
};

const RESPONDING = "Responding";

/** How often the index asks what its apps are doing: at the rate a step changes while any of them
 *  is working, and at the panel's own resting rate when none is. */
export const WORKING_STATUS_MS = 4_000;
const RESTING_STATUS_MS = 30_000;

/** The line a green row prints under its name, and how it is drawn: the engine's own word for the
 *  work in flight shimmers while that work moves, and a turn still waiting for its slot states so
 *  in the resting tone. A row holding no work prints nothing here. */
function activeLine(status: AgentStatus | undefined): { text: string; shimmer: boolean } | null {
  if (status === undefined) return null;
  if (status.turn === "running") return { text: status.activity ?? RESPONDING, shimmer: true };
  if (status.turn === "queued") return { text: "Queued", shimmer: false };
  return null;
}

/** The fact the dot cannot state, held at the pointer the way the chat rail holds a row's facts:
 *  where a resting app sits in time. A row the read has not answered for triggers nothing and
 *  draws no tooltip. */
export function statusLine(status: AgentStatus | undefined): string | null {
  if (status === undefined) return null;
  const now = new Date();
  if (status.turn === "parked") return "Paused";
  if (status.last_failed) return "Last run failed";
  if (status.next_run_at) return "Next run " + friendlyMoment(status.next_run_at, now);
  if (status.last_active_at) return "Active " + friendlyMoment(status.last_active_at, now);
  return "Idle";
}

/** What an app is doing, drawn so a change to it is seen: the words that replace the line are cut
 *  up from under it a character at a time, left to right.
 *
 *  The line itself is the one element across every change, so the sweep over it never restarts and
 *  never breaks — the characters are what the browser replaces, and a character it has just
 *  inserted is what runs the cut, which is why each carries the line it belongs to in its key. The
 *  sweep is painted by the line and clipped to the text under it, its own characters' travel
 *  included.
 *
 *  A screen reader is read the line once, whole: a character per element is a spelling, not a
 *  sentence. */
function ActivityLine({ text, shimmer }: { text: string; shimmer: boolean }) {
  return (
    <span
      className={cn(
        "block w-full truncate font-mono text-small text-ink-soft",
        shimmer && "shimmer",
      )}
    >
      <span className="sr-only">{text}</span>
      <span aria-hidden>
        {Array.from(text, (character, at) => (
          <span key={text + at} data-cut style={{ "--cut": at } as CSSProperties}>
            {character}
          </span>
        ))}
      </span>
    </span>
  );
}

/** The dot the avatar wears, read before any words: the live tone while the app holds work in
 *  flight, the blocked tone when that work stopped wanting the member, and nothing otherwise. */
function statusDot(status: AgentStatus | undefined): string | null {
  if (status === undefined) return null;
  if (status.turn === "running" || status.turn === "queued") return "bg-live";
  if (status.turn === "parked" || status.last_failed) return "bg-blocked";
  return null;
}

/** One app's row: the whole row is the one control, and it opens the app. What acts on the open app
 *  is worn by that app's own pane, beside its name.
 *
 *  The row is one shape whether or not the app is working — a name, and under it a slot the work it
 *  is doing opens. Both lines are held to one line each and cut off where the rail ends: an app
 *  names itself, and the rail is not where either is read in full. The slot is a grid track rather
 *  than a line that appears, so the row's height is a number the browser can move between; the line
 *  that closed it stays drawn behind the fold, since a track collapsing over nothing collapses
 *  instantly. A resting app's mark is held at the pointer instead, where the row costs nothing to
 *  read. */
function AgentRow({
  agent,
  status,
  open,
  pinned,
  onPin,
  onOpen,
}: {
  agent: Agent;
  status: AgentStatus | undefined;
  open: boolean;
  pinned: boolean;
  onPin: () => void;
  onOpen: () => void;
}) {
  const active = activeLine(status);
  /** The line the fold closes over. A track collapsing over nothing collapses instantly, so the
   *  words that were there stay drawn until the fold has shut — and are then dropped, because a
   *  line nobody can see is a line that must not still be animating. */
  const [held, setHeld] = useState(active);
  if (active !== null && (held === null || held.text !== active.text || held.shimmer !== active.shimmer)) {
    setHeld(active);
  }
  const dot = statusDot(status);
  const line = active === null ? statusLine(status) : null;
  const row = (
    <button
      type="button"
      aria-current={open}
      onClick={onOpen}
      className={cn(
        "flex min-h-(--size-row) min-w-0 flex-1 items-center gap-sm border-0 bg-transparent",
        "px-sm py-2xs text-left text-inherit",
      )}
    >
      {/* The mark is the glyph a sidebar row draws, bare in the row's own ink, so the list reads
          as the sidebar the pin act puts a row into. */}
      <span className="relative shrink-0">
        <AgentIcon name={agent.icon} className="size-(--size-glyph)" />
        {/* The dot is always drawn and scales away when the app has nothing to say, so a change of
            state is a mark growing or turning rather than one appearing out of nothing. */}
        <span
          aria-hidden
          className={cn(
            "absolute -right-2xs -bottom-2xs size-sm rounded-full transition duration-200 ease-control",
            dot ?? "scale-0",
          )}
        />
      </span>
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="flex w-full items-baseline gap-sm">
          <span className="min-w-0 truncate text-label">{agentName(agent.name)}</span>
          {agent.main ? <span className="shrink-0 text-small">{MAIN}</span> : null}
        </span>
        <span
          className={cn(
            "grid transition-[grid-template-rows] duration-200 ease-control",
            active === null ? "grid-rows-[0fr]" : "grid-rows-[1fr]",
          )}
          onTransitionEnd={() => {
            if (active === null) setHeld(null);
          }}
        >
          <span className="min-h-0 min-w-0 overflow-hidden">
            {held === null ? null : <ActivityLine text={held.text} shimmer={held.shimmer} />}
          </span>
        </span>
      </span>
    </button>
  );
  return (
    /* The whole row wears the tooltip — anchored on the row, the fact opens past the list's edge
       instead of over the pin act at the row's end, where it would stand between the pointer and
       the control. The row is worn by the same tooltip whether or not it has a fact to hold,
       because an app gains and loses one as it works and a row swapped for another element takes
       the member's focus down with it. A row with nothing to say draws no content and so opens
       nothing — which now takes an app with no purpose and nothing to report, because a shipped
       app always states what it is for. */
    <Tooltip>
      <TooltipTrigger asChild>
        <li
          className={cn("group/row flex items-center rounded-row hover:bg-fill", open && "bg-fill")}
        >
          {row}
          {/* Pinning puts the app's row in the sidebar; the act rests until the pointer is on the
              row, except where it is already done — an unmarked pinned row could only be unpinned
              by a member who already remembered it was pinned. */}
          <button
            type="button"
            aria-label={(pinned ? "Unpin " : "Pin ") + agentName(agent.name)}
            aria-pressed={pinned}
            onClick={onPin}
            className={cn(
              "mr-xs shrink-0 rounded-control border-0 bg-transparent p-2xs text-ink-soft hover:bg-fill",
              pinned
                ? undefined
                : "opacity-0 group-hover/row:opacity-100 focus-visible:opacity-100",
            )}
          >
            {pinned ? (
              <IconPinFilled className="size-icon" aria-hidden />
            ) : (
              <IconPin className="size-icon" aria-hidden />
            )}
          </button>
        </li>
      </TooltipTrigger>
      {agent.purpose || line ? (
        <TooltipContent>
          {/* What the app is for, over what it is doing. A member meeting an app they did not
              install asks the first question before the second, and the row itself has room for
              neither — the name and the activity line are what it holds. */}
          <span className="flex max-w-hint flex-col gap-2xs">
            {agent.purpose ? <span>{agent.purpose}</span> : null}
            {line ? <span className="text-ink-soft">{line}</span> : null}
          </span>
        </TooltipContent>
      ) : null}
    </Tooltip>
  );
}

/** The clock-fired tasks the app holds. Radar reads them across the workspace, beside what they
 *  did; here they are read for the one app they run on. A member writes one on the workspace's own
 *  tasks screen, which is the screen that asks which app runs it — this panel names one app already,
 *  and a second place to write a task is a second place that has to state the same rules. */
const TASK_KIND = "scheduled_task";

/** An app's three reads, standing over the screen they were opened from rather than beside it. The
 *  scrim puts that screen out of focus so the panel is the one thing in hand, and the band names
 *  the read showing with the way to the other two under the same chevron — a member switches reads
 *  without going back to the band they came from. It stays out of the modal state radix would take:
 *  a record opened from the tasks read raises the shared sheet over this panel, and a modal layer
 *  under it would hold the pointer away from that sheet. */
function AppSettings({
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
    <DialogPrimitive.Root modal={false} open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogPrimitive.Portal>
        {/* The scrim is drawn here rather than as the primitive's own overlay, which renders
            nothing outside the modal state this panel stays out of. */}
        <div
          data-slot="app-settings-scrim"
          className="fixed inset-0 z-10 bg-scrim backdrop-blur-scrim animate-appear"
          onClick={onClose}
        />
        <DialogPrimitive.Content
          data-slot="app-settings"
          aria-describedby={undefined}
          onInteractOutside={(event) => event.preventDefault()}
          className={cn(
            "fixed inset-y-0 right-0 left-auto z-10 w-app-settings",
            "flex min-h-0 flex-col border-l border-edge bg-surface pt-2xl",
            "[box-shadow:var(--shadow-raised)] animate-slide-in-end",
          )}
        >
          {/* The band stands the height of the acts on it and carries no rule of its own: the panel
              is one surface, and a line under its own name would part the name from what it names. */}
          <div className="flex h-(--size-control) shrink-0 items-center gap-md px-2xl">
            {/* The app the read belongs to, then the read: a panel standing over the whole screen
                covers the band that would otherwise say which app this is. The app step goes
                nowhere — it is the screen already underneath — so it is the landmark's name and
                text is all it is. */}
            <Breadcrumb className="min-w-0 flex-1">
              <BreadcrumbList className="flex-nowrap text-body tracking-ui">
                <BreadcrumbItem className="min-w-0">
                  <DialogPrimitive.Title asChild>
                    <span className="truncate">{agentName(agent.name)}</span>
                  </DialogPrimitive.Title>
                </BreadcrumbItem>
                <BreadcrumbSeparator />
                <BreadcrumbItem className="min-w-0">
                  <DropdownMenu modal={false}>
                    <DropdownMenuTrigger asChild>
                      <button
                        type="button"
                        className={cn(
                          "flex min-w-0 cursor-pointer items-center gap-xs border-0 bg-transparent p-0",
                          "text-inherit transition-colors duration-100 ease-control hover:text-ink",
                        )}
                      >
                        <BreadcrumbPage>{SETTINGS_TAB_LABELS[tab]}</BreadcrumbPage>
                        <IconChevronDown
                          className="size-(--size-glyph) shrink-0 text-ink-soft"
                          aria-hidden
                        />
                      </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="start" className="w-(--container-menu)">
                      <SettingsTabItems onPick={onTab} />
                    </DropdownMenuContent>
                  </DropdownMenu>
                </BreadcrumbItem>
              </BreadcrumbList>
            </Breadcrumb>
            <Button variant="quiet" size="icon" aria-label="Close" onClick={onClose}>
              <IconX aria-hidden />
            </Button>
          </div>
          <div className="flex min-h-0 flex-1 flex-col gap-2xl overflow-y-auto p-2xl">
            {children}
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

/** The apps index: one row per app under the New application act, the wizard's run in flight at
 *  the head while one is live. The apps screen carried it as a column once; it is the sidebar's
 *  Applications flyout now, mounted only while that flyout is open, which is what scopes the
 *  status polling to the moments the rows are read. */
export function AppsIndex({
  agents,
  openId,
  building,
  pinned,
  onPin,
  onOpen,
  onBuild,
  children,
}: {
  agents: Agent[];
  /** The agent whose pane the page is showing, or null when no app holds it. */
  openId: string | null;
  /** Whether the wizard holds the pane, which draws its row as the place the member already is. */
  building: boolean;
  pinned: string[];
  onPin: (agentId: string) => void;
  onOpen: (agentId: string) => void;
  onBuild: () => void;
  /** Rows the caller lists after the apps — the fixed destinations the sidebar files under
   *  Applications, so the flyout carries the whole section. */
  children?: React.ReactNode;
}) {
  const mainAgent = useMainAgent();
  const [statusRate, setStatusRate] = useState(RESTING_STATUS_MS);
  const statusRead = usePanelRead<{ statuses: AgentStatus[] }>(
    "/api/agents/status",
    0,
    statusRate,
  );
  const statuses: Record<string, AgentStatus> =
    statusRead.phase === "ready"
      ? Object.fromEntries(statusRead.payload.statuses.map((status) => [status.agent_id, status]))
      : {};
  /** A step an app takes is over in seconds, so a line naming one is only true if it is re-read at
   *  that rate — but a screen of resting apps says the same thing every time it is asked, so the
   *  rate rides on whether anything is working at all. */
  const working = Object.values(statuses).some(
    (status) => status.turn === "running" || status.turn === "queued",
  );
  const wanted = working ? WORKING_STATUS_MS : RESTING_STATUS_MS;
  if (statusRead.phase === "ready" && statusRate !== wanted) setStatusRate(wanted);
  /** The index reads most-recent-first: an app holding work in flight sorts as now, a resting one
   *  by its last activity, and the sort is stable, so rows the read has not placed keep the order
   *  the boot read served. */
  const recency = (agent: Agent): number => {
    const status = statuses[agent.id];
    if (status === undefined) return 0;
    if (status.turn === "running" || status.turn === "queued") return Number.MAX_SAFE_INTEGER;
    return status.last_active_at ? Date.parse(status.last_active_at) : 0;
  };
  const ordered = [...agents].sort((a, b) => recency(b) - recency(a));
  // The run lives on the wizard's own store key — busy or spoken before it founds, a forwarding
  // record after — so it survives every unmount of this list; a reload clears the store, so no
  // phantom row survives one.
  const key = mainAgent ? wizardKey(mainAgent.id) : null;
  const held = useChat(key ?? "");
  const running =
    key !== null &&
    !held.closed &&
    (held.busy || (held.messages ?? []).length > 0 || held.founded !== null);
  const runTitle = held.founded?.title ?? null;
  return (
    <nav aria-label="Apps" className="flex min-h-0 flex-1 flex-col gap-sm">
      <div className="flex min-h-0 flex-1 flex-col gap-lg overflow-y-auto">
        <ul className="m-0 flex list-none flex-col gap-px px-sm py-0">
          {/* The run in flight, named the way the wizard's own pane is until the conversation has
              a title of its own. While the pane shows it states where the member already is;
              while an app holds the pane instead, the row is the way back to the run. It is not
              an app: nothing here opens one, and the app's real row arrives from the apps read
              when it lands. */}
          {building || running ? (
            <li className={cn("flex items-center gap-xs rounded-row", building && "bg-fill")}>
              {building ? (
                <div
                  aria-current
                  className="flex min-w-0 flex-1 flex-col gap-2xs px-sm py-xs"
                >
                  <span className="min-w-0 truncate text-label">
                    {runTitle ? APP_BUILDER_TITLE + ": " + runTitle : APP_BUILDER_TITLE}
                  </span>
                  <span className="w-full truncate font-mono text-small text-ink-soft">
                    Building
                  </span>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={onBuild}
                  className={cn(
                    "flex min-w-0 flex-1 flex-col gap-2xs border-0 bg-transparent px-sm py-xs",
                    "rounded-row text-left text-inherit hover:bg-fill",
                  )}
                >
                  <span className="min-w-0 max-w-full truncate text-label">
                    {runTitle ? APP_BUILDER_TITLE + ": " + runTitle : APP_BUILDER_TITLE}
                  </span>
                  <span className="w-full truncate font-mono text-small text-ink-soft">
                    Building
                  </span>
                </button>
              )}
            </li>
          ) : null}
          {ordered.map((agent) => (
            <AgentRow
              key={agent.id}
              agent={agent}
              status={statuses[agent.id]}
              open={!building && agent.id === openId}
              pinned={pinned.includes(agent.id)}
              onPin={() => onPin(agent.id)}
              onOpen={() => onOpen(agent.id)}
            />
          ))}
          {children}
        </ul>
      </div>
      {/* Every member is offered the act: the `agent` kind admits a create from any speaking
          member and stamps them the owner, and the wizard rides the main agent's own chat. */}
      {mainAgent ? (
        <Button variant="send" size="bar" className="mx-sm shrink-0" onClick={onBuild}>
          New application
        </Button>
      ) : null}
    </nav>
  );
}

/** The apps screen: the selected app's pane whole — the main app on the bare route — or the
 *  app-building wizard at its own address; switching apps is the sidebar's Applications flyout. */
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
