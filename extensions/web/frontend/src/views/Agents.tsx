import { useState, type CSSProperties } from "react";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ObjectPane } from "@/kernel/objects";
import { BesideHost } from "@/kernel/beside";
import { useDrawerList, useShutDrawer } from "@/kernel/drawer";
import { usePanelRead } from "@/kernel/panel";
import { BANDS } from "@/kernel/pane";
import { TabPanel, TabRow } from "@/kernel/tabs";
import { AgentIcon } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";
import { chatState, clearChat, updateChat, useChat } from "@/lib/chatStore";
import { cn } from "@/lib/cn";
import { useMainAgent } from "@/lib/mainAgent";
import { friendlyMoment } from "@/lib/moments";
import { AgentPane } from "@/views/AgentPane";
import { AgentSkills } from "@/views/AgentSkills";
import { APP_BUILDER_TITLE, AppBuilder, wizardKey } from "@/views/AppBuilder";
import { AgentConnectors } from "@/views/Connectors";
import { Settings } from "@/views/Settings";
import type { PlaceStep, WorkspacePlace } from "@/lib/route";
import type { ChatRow } from "@/lib/rail";
import type { Agent, Member } from "@/lib/types";

export type AgentsProps = {
  agents: Agent[];
  member: Member;
  /** The agent the hash names, or null on the bare route — which shows the main agent without
   *  navigating. */
  selected: Agent | null;
  chats: ChatRow[] | null;
  place: WorkspacePlace;
  onOpen: (agentId: string) => void;
  onPlace: (place: WorkspacePlace, step: PlaceStep) => void;
  onCreated: (agent: Agent, conversationId: string, title: string) => void;
  onAgents: () => void;
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
  onOpen,
}: {
  agent: Agent;
  status: AgentStatus | undefined;
  open: boolean;
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
      <span className="relative shrink-0">
        <Avatar>
          <AvatarFallback>
            <AgentIcon name={agent.icon} />
          </AvatarFallback>
        </Avatar>
        {/* The mark is always drawn and scales away when the app has nothing to say, so a change of
            state is a mark growing or turning rather than one appearing out of nothing. */}
        <span
          aria-hidden
          className={cn(
            "absolute right-0 bottom-0 size-sm rounded-full outline-2 transition duration-200 ease-control",
            open ? "outline-fill" : "outline-sidebar group-hover/row:outline-fill",
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
    <li className={cn("group/row flex items-center rounded-row hover:bg-fill", open && "bg-fill")}>
      {/* The row is worn by the same tooltip whether or not it has a fact to hold, because an app
          gains and loses one as it works and a row swapped for another element takes the member's
          focus down with it. A row with nothing to say draws no content and so opens nothing. */}
      <Tooltip>
        <TooltipTrigger asChild>{row}</TooltipTrigger>
        {line ? <TooltipContent>{line}</TooltipContent> : null}
      </Tooltip>
    </li>
  );
}

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

/** The apps screen: a thin index — one row per app under the New application act, the open app's
 *  settings behind the gear beside it — next to a wide pane holding the selected app, or the
 *  app-building wizard while a run is open. On a narrow screen the pane is the page and the nav
 *  drawer holds the index. */
export function Agents({
  agents,
  member,
  selected,
  chats,
  place,
  onOpen,
  onPlace,
  onCreated,
  onAgents,
}: AgentsProps) {
  const mainAgent = useMainAgent();
  const shown = selected ?? mainAgent;
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
  // record after — so it survives every unmount of this screen; a reload clears the store, so no
  // phantom row survives one. `wanting` is only the member's last press: it raises the pane ahead
  // of the run's first send and brings it back over a selected app.
  const key = mainAgent ? wizardKey(mainAgent.id) : null;
  const held = useChat(key ?? "");
  const running =
    key !== null &&
    !held.closed &&
    (held.busy || (held.messages ?? []).length > 0 || held.founded !== null);
  const [wanting, setWanting] = useState(false);
  const building = mainAgent !== null && (wanting || (running && selected === null));
  const runTitle = held.founded?.title ?? null;
  const [settling, setSettling] = useState(false);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>(SETTINGS_TABS[0]);
  const shutDrawer = useShutDrawer();
  /** Raising the wizard draws it on the page, so a drawer holding the index that raised it is shut
   *  the way navigating out of the drawer shuts it. */
  const build = () => {
    setWanting(true);
    shutDrawer();
  };

  const index = useDrawerList(
    <div
      className={cn(
        "flex min-h-0 flex-col",
        "border-r border-edge bg-sidebar py-2xl max-narrow:border-r-0 max-narrow:py-0",
      )}
    >
      {/* The list carries the column's scroll so the act below it stands at the bottom edge
          however many apps the workspace holds. */}
      <nav aria-label="Agents" className="flex min-h-0 flex-1 flex-col gap-sm">
        <ul className="m-0 flex min-h-0 flex-1 list-none flex-col gap-px overflow-y-auto px-sm py-0">
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
                  onClick={build}
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
              open={!building && agent.id === shown?.id}
              onOpen={() => {
                setWanting(false);
                onOpen(agent.id);
              }}
            />
          ))}
        </ul>
        {/* Every member is offered the act: the `agent` kind admits a create from any speaking
            member and stamps them the owner, and the wizard rides the main agent's own chat. */}
        {mainAgent ? (
          <Button variant="send" size="bar" className="mx-sm shrink-0" onClick={build}>
            New application
          </Button>
        ) : null}
      </nav>
    </div>,
  );

  return (
    <div className="relative grid min-h-0 min-w-0 flex-1 grid-cols-[var(--container-sidebar)_1fr] max-narrow:grid-cols-1">
      {index}
      {building && mainAgent ? (
        <AppBuilder
          agent={mainAgent}
          member={member}
          onSettled={onAgents}
          onClose={() => {
            // Closing ends the run's presence here. A run whose founding send is still in flight
            // cannot be cleared out from under that send, so the key wears the close instead and
            // the landing leaves no forwarding record behind.
            setWanting(false);
            if (key === null) return;
            if (chatState(key).busy) updateChat(key, (state) => ({ ...state, closed: true }));
            else clearChat(key);
          }}
        />
      ) : shown ? (
        <AgentPane
          key={shown.id}
          agent={shown}
          member={member}
          chats={chats}
          onCreated={(conversationId, title) => onCreated(shown, conversationId, title)}
          onSettings={() => {
            setSettingsTab(SETTINGS_TABS[0]);
            setSettling(true);
          }}
          place={place}
          onPlace={onPlace}
        />
      ) : (
        <div className="m-auto max-w-empty text-center text-ink-soft">
          No agent is visible to you.
        </div>
      )}
      {shown && !building ? (
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
