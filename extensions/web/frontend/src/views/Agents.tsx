import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import {
  IconChevronDown,
  IconPin,
  IconPinFilled,
  IconX,
} from "@tabler/icons-react";

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
import { Ticker } from "@/components/ui/ticker";
import { ObjectPane } from "@/kernel/objects";
import { AgentIcon } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";
import { statusDot, useAppStatus, type AgentStatus } from "@/lib/appStatusStore";
import { chatState, clearChat, updateChat, useChat } from "@/lib/chatStore";
import { cn } from "@/lib/cn";
import { useMainAgent } from "@/lib/mainAgent";
import { openApps } from "@/lib/router";
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
import type { PlaceStep, WorkspacePlace } from "@/lib/route";
import { appOrder, type ChatRow } from "@/lib/rail";
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


const RESPONDING = "Responding";

/** The line a green row prints under its name, and how it is drawn: the engine's own word for the
 *  work in flight shimmers while that work moves, and a turn still waiting for its slot states so
 *  in the resting tone. A row holding no work prints nothing here. */
function activeLine(status: AgentStatus | undefined): { text: string; shimmer: boolean } | null {
  if (status === undefined) return null;
  if (status.turn === "running") return { text: status.activity ?? RESPONDING, shimmer: true };
  if (status.turn === "queued") return { text: "Queued", shimmer: false };
  return null;
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
function ActivityLine({
  asks,
  text,
  shimmer,
}: {
  asks: number;
  text: string;
  shimmer: boolean;
}) {
  return (
    <Ticker asks={asks} className={cn("font-mono text-small text-ink-soft", shimmer && "shimmer")}>
      <span className="sr-only">{text}</span>
      <span aria-hidden>
        {Array.from(text, (character, at) => (
          <span key={text + at} data-cut style={{ "--cut": at } as CSSProperties}>
            {character}
          </span>
        ))}
      </span>
    </Ticker>
  );
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
/** One app's row: the whole row is the one control, and it opens the app. What acts on the open app
 *  is worn by that app's own pane, beside its name.
 *
 *  The row states what it knows in the row itself. An app's work is the fact the sidebar exists to
 *  carry, and a fact held at the pointer is a fact the member has to go asking for — so the name
 *  takes the first line and what the app is doing takes the second, in the resting tone under it.
 *  The slot is a grid track rather than a line that appears, so the row's height is a number the
 *  browser can move between; the line that closed it stays drawn behind the fold, since a track
 *  collapsing over nothing collapses instantly.
 *
 *  That second line is for work in flight and nothing else. A column whose every row printed a
 *  standing line would state the app doing something and the app doing nothing in the same weight,
 *  and the row that matters would stop being the one that catches the eye. What a resting app has
 *  to say — paused, or its last run failed — is said by the dot, which costs the column no height.
 *  Both lines are held to the column and state their tails by travelling while the member is on the
 *  row, the way a conversation's title does. */
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
  const [asks, setAsks] = useState(0);
  /* The app the member is standing in states its own work, in the pane, at length. The row saying
     it again under the name is the same fact twice on one screen — so the open row keeps the dot,
     which is the part the pane's own words cannot carry, and drops the line. */
  const said = open ? null : activeLine(status);
  /** The line the fold closes over. A track collapsing over nothing collapses instantly, so the
   *  words that were there stay drawn until the fold has shut — and are then dropped, because a
   *  line nobody can see is a line that must not still be animating. */
  const [held, setHeld] = useState(said);
  if (said !== null && (held === null || held.text !== said.text || held.shimmer !== said.shimmer)) {
    setHeld(said);
  }
  const dot = statusDot(status, agent.setup_due === true);
  return (
    <li className={cn("group/row flex items-center rounded-row hover:bg-fill", open && "bg-fill")}>
      <button
        type="button"
        aria-current={open}
        onClick={onOpen}
        onPointerEnter={() => setAsks((asked) => asked + 1)}
        onPointerLeave={() => setAsks(0)}
        onFocus={() => setAsks((asked) => asked + 1)}
        onBlur={() => setAsks(0)}
        className={cn(
          "flex min-h-(--size-row) min-w-0 flex-1 items-center gap-md border-0 bg-transparent",
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
          <Ticker asks={asks} className="text-label">
            {agentName(agent.name)}
          </Ticker>
          <span
            className={cn(
              "grid transition-[grid-template-rows] duration-200 ease-control",
              said === null ? "grid-rows-[0fr]" : "grid-rows-[1fr]",
            )}
            onTransitionEnd={(event) => {
              /* The line inside this track travels on its own transition, and that one bubbles
                 here too. Only the track's own end means the fold has shut. */
              if (event.target !== event.currentTarget) return;
              if (said === null) setHeld(null);
            }}
          >
            <span className="min-h-0 min-w-0 overflow-hidden">
              {held === null ? null : (
                <ActivityLine asks={asks} text={held.text} shimmer={held.shimmer} />
              )}
            </span>
          </span>
        </span>
      </button>
      {/* Pinning moves the app up the sidebar's order. The act rests until the pointer is on the
          row whether or not it is already done: a mark standing on every pinned row is a column of
          controls nobody is using, and where the pinned rows lead the column that is most of it.
          What the pin did is read off the order, which is the thing it changed. */}
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
    </li>
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

/** The apps index: the workspace's apps as the sidebar draws them, the wizard's run in flight at
 *  the head while one is live. What each app is doing comes from `appStatusStore`, which every
 *  reader of the rows shares.
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
  building,
  pinned,
  onPin,
  onOpen,
  onBuild,
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
}) {
  const mainAgent = useMainAgent();
  const { statuses } = useAppStatus();
  const shown = appOrder(agents, pinned);
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
    <nav aria-label="Apps" className="flex min-h-0 flex-col">
      <div className="flex min-h-0 max-h-(--size-apps-open) flex-col overflow-y-auto">
        <ul className="m-0 flex list-none flex-col gap-px p-0">
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
                    {runTitle ? APP_CREATOR_TITLE + ": " + runTitle : APP_CREATOR_TITLE}
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
                    {runTitle ? APP_CREATOR_TITLE + ": " + runTitle : APP_CREATOR_TITLE}
                  </span>
                  <span className="w-full truncate font-mono text-small text-ink-soft">
                    Building
                  </span>
                </button>
              )}
            </li>
          ) : null}
          {shown.map((agent) => (
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
        </ul>
      </div>
    </nav>
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

/** An app's pane whole. */
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
