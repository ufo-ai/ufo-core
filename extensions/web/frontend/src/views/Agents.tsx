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

function activeLine(status: AgentStatus | undefined): { text: string; shimmer: boolean } | null {
  if (status === undefined) return null;
  if (status.turn === "running") return { text: status.activity ?? RESPONDING, shimmer: true };
  if (status.turn === "queued") return { text: "Queued", shimmer: false };
  return null;
}

/** A character the browser has just inserted is what runs the cut, which is why each carries the line
 *  it belongs to in its key. A screen reader is read the line once, whole. */
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
  const said = open ? null : activeLine(status);
  /** A track collapsing over nothing collapses instantly, so the words that were there stay drawn until
   *  the fold has shut. */
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
              /* The line inside this track travels on its own transition, and that one bubbles here too. Only the
                 track's own end means the fold has shut. */
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
    <DialogPrimitive.Root modal={false} open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogPrimitive.Portal>
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
          <div className="flex h-(--size-control) shrink-0 items-center gap-md px-2xl">
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
  openId: string | null;
  building: boolean;
  pinned: string[];
  onPin: (agentId: string) => void;
  onOpen: (agentId: string) => void;
  onBuild: () => void;
}) {
  const mainAgent = useMainAgent();
  const { statuses } = useAppStatus();
  const shown = appOrder(agents, pinned);
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
