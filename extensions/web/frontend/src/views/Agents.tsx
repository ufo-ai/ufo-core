import { useEffect, useState, type ReactNode } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { IconChevronDown, IconX } from "@tabler/icons-react";

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
