import { useCallback, useEffect, useRef, useState } from "react";
import {
  IconAdjustments,
  IconBrandSlack,
  IconDeviceDesktop,
  IconEdit,
  IconFolder,
  IconLayoutSidebarRight,
  IconMoon,
  IconRadar,
  IconSettings,
  IconSparkles,
  IconSun,
  IconTerminal2,
  IconUsers,
} from "@tabler/icons-react";

import logo from "@/assets/ufo-logo.svg";
import { SILENT, Toast, type ToastState } from "@/components/ui/toast";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { Admin } from "@/views/Admin";
import { Agents } from "@/views/Agents";
import { ChatPane } from "@/views/ChatPane";
import { ConversationSlotPane } from "@/views/ConversationSlotPane";
import { ConversationDetail, Disclose, subject } from "@/views/Conversations";
import { SignIn } from "@/views/SignIn";
import { TabbedPane } from "@/views/TabbedPane";
import { SECTION_VIEWS, WORKSPACE_VIEWS } from "@/views/registry";
import {
  SLACK_SURFACE,
  UFO_SURFACE,
  WEB_SURFACE,
  isPortalChat,
  Viewer,
  origin,
  slackLink,
  speakerName,
  surfaceWord,
  useViewer,
} from "@/lib/audience";
import { COLUMN, Pane, PaneHeader } from "@/kernel/pane";
import { MainAgentProvider } from "@/lib/mainAgent";
import { getJson } from "@/lib/api";
import { cn } from "@/lib/cn";
import { SCHEME_OPTIONS, heldScheme, holdScheme, type Scheme } from "@/lib/scheme";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  bumpChat,
  mergeChats,
  railGroups,
  stampIso,
  type ChatRow,
  type ChatsPayload,
  type RailSort,
} from "@/lib/rail";
import {
  SECTIONS,
  WORKSPACE_TABS,
  agentHash,
  artifactTarget,
  bootRoute,
  chatHash,
  newChatHash,
  parseHash,
  sectionHash,
  workspaceHash,
  type AgentTab,
  type PlaceStep,
  type Route,
  type Section,
  type WorkspacePlace,
  type WorkspaceTab,
} from "@/lib/route";
import type { Agent, Member, NewAgentForm, OwnedConversation, Subagent } from "@/lib/types";

export type AppProps = {
  agents: Agent[];
  subagents: Subagent[];
  member: Member;
  newAgent: NewAgentForm | null;
  onAgents: () => void;
};

type Rail = { phase: "loading" | "failed" | "ready"; rows: ChatRow[] };

const PICKER_ID = "new-conversation-agents";

type Sought =
  | { kind: "answered" }
  | { kind: "signed-out" }
  | { kind: "failed"; message: string };

export function App({ agents, subagents, member, newAgent, onAgents }: AppProps) {
  const [route, setRoute] = useState<Route>(() => bootRoute(location.hash, location.search));
  useEffect(() => {
    const target = artifactTarget(location.search);
    if (target) location.replace(target);
  }, []);
  const [rail, setRail] = useState<Rail>({ phase: "loading", rows: [] });
  const [toast, setToast] = useState<ToastState>(SILENT);
  const [reloads, setReloads] = useState(0);
  const [sought, setSought] = useState<Readonly<Record<string, Sought>>>({});
  const [linked, setLinked] = useState<Record<string, OwnedConversation>>({});
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem("sidebar") === "collapsed");
  const setSidebarCollapsed = useCallback((next: boolean) => {
    setCollapsed(next);
    localStorage.setItem("sidebar", next ? "collapsed" : "expanded");
  }, []);
  const [railSort, setRailSort] = useState<RailSort>(() =>
    localStorage.getItem("rail-sort") === "agent" ? "agent" : "recency",
  );
  const setRailSortHeld = useCallback((next: RailSort) => {
    setRailSort(next);
    localStorage.setItem("rail-sort", next);
  }, []);
  const mainAgent = agents.find((agent) => agent.main) ?? agents[0] ?? null;
  const routeRef = useRef(route);
  routeRef.current = route;

  useEffect(() => {
    const booted = routeRef.current;
    if (!location.hash && booted.kind === "chat") {
      history.replaceState(null, "", chatHash(booted.conversationId));
    }
    const onHash = () => setRoute(parseHash(location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    let live = true;
    setRail((current) => ({ phase: "loading", rows: current.rows }));
    getJson<ChatsPayload>("/api/chats").then((result) => {
      if (!live) return;
      if (!result.ok && result.status !== 401) {
        setToast({ title: "Conversations did not refresh.", description: result.message });
      }
      setRail((current) =>
        result.ok
          ? { phase: "ready", rows: mergeChats(result.payload.chats, current.rows) }
          : { phase: "failed", rows: current.rows },
      );
    });
    return () => {
      live = false;
    };
  }, [reloads]);

  const go = useCallback((hash: string, next: Route) => {
    if (location.hash !== hash) location.hash = hash;
    setRoute(next);
  }, []);

  const openChat = useCallback(
    (conversationId: string) => go(chatHash(conversationId), { kind: "chat", conversationId }),
    [go],
  );

  const openNewChat = useCallback(
    (agentId: string) => go(newChatHash(agentId), { kind: "new-chat", agentId }),
    [go],
  );

  const openAgents = useCallback(() => go("#/agents", { kind: "agents" }), [go]);

  const openAgent = useCallback(
    (agentId: string, tab: AgentTab = "home") =>
      go(agentHash(agentId, tab), { kind: "agent", agentId, tab, place: {} }),
    [go],
  );

  const stepPlace = useCallback(
    (step: PlaceStep, held: boolean, next: () => { route: Route; hash: string } | null) => {
      if (step !== "push" && !held) return;
      if (step === "back") {
        history.back();
        return;
      }
      const target = next();
      if (!target) return;
      if (step === "replace") {
        history.replaceState(null, "", target.hash);
        setRoute(target.route);
        return;
      }
      go(target.hash, target.route);
    },
    [go],
  );

  const placeAgent = useCallback(
    (tab: AgentTab, place: WorkspacePlace, step: PlaceStep) => {
      const seen = routeRef.current;
      stepPlace(step, seen.kind === "agent" && seen.tab === tab, () =>
        seen.kind === "agent"
          ? {
              route: { kind: "agent", agentId: seen.agentId, tab, place },
              hash: agentHash(seen.agentId, tab, place),
            }
          : null,
      );
    },
    [stepPlace],
  );

  const openSlot = useCallback(
    (conversationId: string, slot: string | null) =>
      go(chatHash(conversationId, slot ?? undefined), {
        kind: "chat",
        conversationId,
        ...(slot ? { slot } : {}),
      }),
    [go],
  );

  const placeWorkspace = useCallback(
    (view: WorkspaceTab, place: WorkspacePlace, step: PlaceStep) => {
      const seen = routeRef.current;
      stepPlace(step, seen.kind === "workspace" && seen.view === view, () => ({
        route: { kind: "workspace", view, place },
        hash: workspaceHash(view, place),
      }));
    },
    [stepPlace],
  );

  const placeSection = useCallback(
    (section: Section, place: WorkspacePlace, step: PlaceStep) => {
      const seen = routeRef.current;
      stepPlace(step, seen.kind === "section" && seen.section === section, () => ({
        route: { kind: "section", section, place },
        hash: sectionHash(section, place),
      }));
    },
    [stepPlace],
  );

  const openAdmin = useCallback(() => go("#/admin", { kind: "admin" }), [go]);

  const created = useCallback(
    (agent: Agent, conversationId: string, title: string) => {
      const row: ChatRow = {
        conversation_id: conversationId,
        agent_id: agent.id,
        agent_name: agent.name,
        title,
        last_at: stampIso(new Date()),
        surface: WEB_SURFACE,
        surface_label: null,
        mine: true,
        speaker: null,
      };
      setRail((current) => ({ ...current, rows: mergeChats(current.rows, [row]) }));
      const seen = routeRef.current;
      const started =
        seen.kind === "home" || (seen.kind === "new-chat" && seen.agentId === agent.id);
      if (started) openChat(conversationId);
    },
    [openChat],
  );

  const activity = useCallback((conversationId: string) => {
    setRail((current) => ({
      ...current,
      rows: bumpChat(current.rows, conversationId, new Date()),
    }));
  }, []);

  useEffect(() => {
    if (route.kind !== "chat" || rail.phase !== "ready") return;
    const wanted = route.conversationId;
    if (
      rail.rows.some((row) => row.conversation_id === wanted && isPortalChat(row.surface)) ||
      wanted in sought
    ) {
      return;
    }
    let live = true;
    getJson<ChatsPayload>("/api/chats?conversation=" + wanted).then((result) => {
      if (!live) return;
      const outcome: Sought = result.ok
        ? { kind: "answered" }
        : result.status === 401
          ? { kind: "signed-out" }
          : { kind: "failed", message: result.message };
      setSought((current) => ({ ...current, [wanted]: outcome }));
      if (!result.ok) return;
      if (result.payload.chats.length) {
        setRail((current) => ({ ...current, rows: mergeChats(current.rows, result.payload.chats) }));
      }
      const conversation = result.payload.conversation;
      if (conversation) {
        setLinked((current) => ({ ...current, [wanted]: conversation }));
      }
    });
    return () => {
      live = false;
    };
  }, [route, rail, sought]);

  return (
    <Viewer.Provider value={member.email}>
      <MainAgentProvider agents={agents}>
        <div className={cn("grid h-dvh max-narrow:grid-cols-1 max-narrow:grid-rows-[auto_1fr]", collapsed ? "grid-cols-[var(--container-rail)_1fr]" : "grid-cols-[var(--container-sidebar)_1fr]")}>
          <TooltipProvider>
          <nav
            aria-label="Workspace"
            className="flex min-h-0 flex-col gap-sm border-r border-edge bg-sidebar py-2xl max-narrow:flex-row max-narrow:items-center max-narrow:gap-0 max-narrow:border-r-0 max-narrow:border-b max-narrow:py-0">
            <div className={cn("flex h-(--size-row) items-center justify-between pl-2xl pr-md max-narrow:h-auto max-narrow:px-lg max-narrow:py-md", collapsed && "justify-center px-sm max-narrow:justify-between max-narrow:px-lg")}>
              <span
                role="img"
                aria-label="ufo"
                className={cn(
                  "h-(--size-wordmark) w-(--size-logo) bg-current",
                  collapsed && "hidden max-narrow:block",
                )}
                style={{ mask: `url(${logo}) center / contain no-repeat` }}
              />
              <SidebarTooltip collapsed={collapsed} label="Expand sidebar">
                <SidebarToggle
                  label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
                  onClick={() => setSidebarCollapsed(!collapsed)}
                />
              </SidebarTooltip>
            </div>
            <ul className="m-0 flex list-none flex-col gap-px px-sm py-0 max-narrow:flex-row max-narrow:items-center max-narrow:overflow-x-auto max-narrow:p-0">
              <li>
                <NewChat agents={agents} mainAgent={mainAgent} collapsed={collapsed} onExpand={() => setSidebarCollapsed(false)} onNewChat={openNewChat} />
              </li>
              <li>
                <NavRow
                  icon={<AgentsGlyph />}
                  current={route.kind === "agents" || route.kind === "agent"}
                  collapsed={collapsed}
                  label="Agents"
                  onClick={openAgents}
                >
                  Agents
                </NavRow>
              </li>
              {SECTIONS.map((section) => (
                <li key={section}>
                  <NavRow
                    icon={SECTION_GLYPHS[section]}
                    current={route.kind === "section" && route.section === section}
                    collapsed={collapsed}
                    label={SECTION_VIEWS[section].label}
                    onClick={() => placeSection(section, {}, "push")}
                  >
                    {SECTION_VIEWS[section].label}
                  </NavRow>
                </li>
              ))}
              <li>
                <NavRow
                  icon={<WorkspaceGlyph />}
                  current={route.kind === "workspace"}
                  collapsed={collapsed}
                  label="Workspace"
                  onClick={() => placeWorkspace("team", {}, "push")}
                >
                  Workspace
                </NavRow>
              </li>
            </ul>
            <div className={cn("flex min-h-0 flex-1 flex-col gap-sm overflow-y-auto px-sm max-narrow:flex-row max-narrow:items-center max-narrow:overflow-y-hidden max-narrow:overflow-x-auto max-narrow:p-0", collapsed && "hidden max-narrow:flex")}>
              <RailList
                rail={rail}
                route={route}
                mainAgent={mainAgent}
                sort={railSort}
                onSort={setRailSortHeld}
                onOpen={openChat}
                onRetry={() => setReloads((count) => count + 1)}
              />
            </div>
            <footer className={cn("flex items-center gap-sm px-2xl max-narrow:ml-auto max-narrow:px-lg max-narrow:py-md", collapsed && "mt-auto justify-center px-sm max-narrow:mt-0 max-narrow:px-lg")}>
              <SidebarTooltip collapsed={collapsed} label={member.email}>
                <span
                  aria-hidden="true"
                  className="flex size-(--size-avatar) shrink-0 items-center justify-center rounded-full bg-fill text-small max-narrow:hidden"
                >
                  {member.email.slice(0, 1).toUpperCase()}
                </span>
              </SidebarTooltip>
              <span className={cn("flex min-w-0 flex-1 flex-col max-narrow:hidden", collapsed && "hidden")}>
                <span className="truncate text-label">{member.email}</span>
                <span className="text-small text-ink-soft">
                  {member.admin ? "Admin" : "Member"}
                </span>
              </span>
              <SchemePick collapsed={collapsed} />
              {member.admin ? (
                <button
                  type="button"
                  aria-label="Administration"
                  onClick={openAdmin}
                  className={cn(
                    "rounded-control border-0 bg-transparent p-2xs text-ink-soft hover:bg-fill",
                    collapsed && "hidden max-narrow:block",
                  )}
                >
                  <SettingsGlyph />
                </button>
              ) : null}
            </footer>
          </nav>
          </TooltipProvider>
          <RoutedPane
            route={route}
            agents={agents}
            subagents={subagents}
            member={member}
            newAgent={newAgent}
            mainAgent={mainAgent}
            rail={rail}
            onAgents={onAgents}
            onCreated={created}
            onActivity={activity}
            onOpenAgent={openAgent}
            onOpenSlot={openSlot}
            onAgentsIndex={openAgents}
            onPlaceWorkspace={placeWorkspace}
            onPlaceSection={placeSection}
            onPlaceAgent={placeAgent}
            sought={sought}
            linked={linked}
          />
          <Toast state={toast} onDone={() => setToast(SILENT)} />
        </div>
      </MainAgentProvider>
    </Viewer.Provider>
  );
}

function RoutedPane({
  route,
  agents,
  subagents,
  member,
  newAgent,
  mainAgent,
  rail,
  onAgents,
  onCreated,
  onActivity,
  onOpenAgent,
  onOpenSlot,
  onAgentsIndex,
  onPlaceWorkspace,
  onPlaceSection,
  onPlaceAgent,
  sought,
  linked,
}: {
  route: Route;
  agents: Agent[];
  subagents: Subagent[];
  member: Member;
  newAgent: NewAgentForm | null;
  mainAgent: Agent | null;
  rail: Rail;
  onAgents: () => void;
  onCreated: (agent: Agent, conversationId: string, title: string) => void;
  onActivity: (conversationId: string) => void;
  onOpenAgent: (agentId: string, tab?: AgentTab) => void;
  onOpenSlot: (conversationId: string, slot: string | null) => void;
  onAgentsIndex: () => void;
  onPlaceWorkspace: (view: WorkspaceTab, place: WorkspacePlace, step: PlaceStep) => void;
  onPlaceSection: (section: Section, place: WorkspacePlace, step: PlaceStep) => void;
  onPlaceAgent: (tab: AgentTab, place: WorkspacePlace, step: PlaceStep) => void;
  sought: Readonly<Record<string, Sought>>;
  linked: Readonly<Record<string, OwnedConversation>>;
}) {
  if (route.kind === "admin") return <Admin />;
  if (route.kind === "bad-link") return <PaneNote>This conversation link is not valid.</PaneNote>;
  if (route.kind === "workspace") {
    return (
      <TabbedPane
        title="Workspace"
        group="workspace"
        tabs={WORKSPACE_TABS}
        views={WORKSPACE_VIEWS}
        view={route.view}
        place={route.place}
        onPlace={onPlaceWorkspace}
      />
    );
  }
  if (route.kind === "section") {
    return (
      <TabbedPane
        title={SECTION_VIEWS[route.section].label}
        group="section"
        tabs={[route.section]}
        views={SECTION_VIEWS}
        view={route.section}
        place={route.place}
        onPlace={onPlaceSection}
      />
    );
  }
  if (route.kind === "agents" || route.kind === "agent") {
    const selected =
      route.kind === "agent" ? (agents.find((entry) => entry.id === route.agentId) ?? null) : null;
    if (route.kind === "agent" && !selected) return <PaneNote>No such agent.</PaneNote>;
    const shown = selected ?? mainAgent;
    return (
      <Pane>
        <Agents
          agents={agents}
          subagents={subagents}
          newAgent={newAgent}
          selected={selected}
          tab={route.kind === "agent" ? route.tab : "home"}
          place={route.kind === "agent" ? route.place : {}}
          onOpen={onOpenAgent}
          onTab={(tab) => (shown ? onOpenAgent(shown.id, tab) : undefined)}
          onPlace={(place, step) =>
            route.kind === "agent" ? onPlaceAgent(route.tab, place, step) : undefined
          }
          onAgents={onAgents}
        />
      </Pane>
    );
  }
  if (route.kind === "conversation-slot") {
    const agent = agents.find((entry) => entry.id === route.agentId);
    if (!agent) return <PaneNote>No such agent.</PaneNote>;
    return (
      <ConversationSlotPane
        agent={agent}
        conversationId={route.conversationId}
        slot={route.slot}
        rootConversationId={route.rootConversationId}
        onOpenAgent={onOpenAgent}
      />
    );
  }
  if (route.kind === "chat") {
    const row = rail.rows.find(
      (entry) => entry.conversation_id === route.conversationId && isPortalChat(entry.surface),
    );
    const linkedConversation = linked[route.conversationId];
    if (!row && linkedConversation) {
      const linkedAgent = agents.find((entry) => entry.id === linkedConversation.agent.id);
      if (!linkedAgent) return <PaneNote>No such agent.</PaneNote>;
      if (!linkedConversation.readable && !linkedConversation.disclosable) return <NotShared />;
      return (
        <LinkedPane
          key={linkedConversation.id}
          agent={linkedAgent}
          conversation={linkedConversation}
          slot={route.slot}
          onSelectSlot={(slot) => onOpenSlot(route.conversationId, slot)}
          onAgentsIndex={onAgentsIndex}
        />
      );
    }
    const listedAgent = row ? agents.find((entry) => entry.id === row.agent_id) : undefined;
    const agent =
      listedAgent ??
      (row && row.surface.startsWith("extension:")
        ? {
            id: row.agent_id,
            name: row.agent_name,
            model: row.agent_model ?? "",
            main: false,
          }
        : undefined);
    if (!row || !agent) {
      if (rail.phase === "loading") return <PaneNote>Loading…</PaneNote>;
      if (rail.phase === "failed") return <PaneNote>Couldn't load conversations.</PaneNote>;
      const outcome = sought[route.conversationId];
      if (!outcome) return <PaneNote>Loading…</PaneNote>;
      if (outcome.kind === "signed-out") {
        return (
          <Pane className={COLUMN}>
            <SignIn />
          </Pane>
        );
      }
      if (outcome.kind === "failed") return <PaneNote>{outcome.message}</PaneNote>;
      return <NotShared />;
    }
    return (
      <ChatPane
        key={row.conversation_id}
        agent={agent}
        member={member}
        conversationId={row.conversation_id}
        onActivity={onActivity}
        onAgentsIndex={onAgentsIndex}
        conversationOnly={!listedAgent}
        slot={route.slot}
        onSelectSlot={(slot) => onOpenSlot(route.conversationId, slot)}
      />
    );
  }
  const agent =
    route.kind === "new-chat"
      ? agents.find((entry) => entry.id === route.agentId)
      : (mainAgent ?? undefined);
  if (!agent) return <PaneNote>No such agent.</PaneNote>;
  return (
    <ChatPane
      key={"new:" + agent.id}
      agent={agent}
      member={member}
      conversationId={null}
      onCreated={(conversationId, title) => onCreated(agent, conversationId, title)}
      onActivity={onActivity}
      onAgentsIndex={onAgentsIndex}
    />
  );
}

/** A conversation another surface holds, read in the portal: the thread pane a portal chat wears,
 *  headed the same way — the agent it ran under and the model it ran on, reached back out of the
 *  same way — with the surface it is happening on marked at the far end of that header. The header
 *  is what says where the member is, so the transcript under it is headed by what the conversation
 *  is called and nothing leads back out twice. */
function LinkedPane({
  agent,
  conversation,
  slot,
  onSelectSlot,
  onAgentsIndex,
}: {
  agent: Agent;
  conversation: OwnedConversation;
  slot?: string;
  onSelectSlot: (slot: string | null) => void;
  onAgentsIndex: () => void;
}) {
  const [disclosed, setDisclosed] = useState(false);
  const viewer = useViewer();
  const readable = conversation.readable || disclosed;
  return (
    <Pane>
      <div
        className={cn(
          "relative grid min-h-0 flex-1 grid-cols-1",
          readable && slot && "grid-cols-(--grid-slot) max-narrow:grid-cols-1",
        )}
      >
        <div className="flex min-h-0 min-w-0 flex-col">
          <PaneHeader
            parent={{ label: "Agents", onGo: onAgentsIndex }}
            current={agent.name}
            note={<span className="font-mono text-mono text-ink-soft">{agent.model}</span>}
            actions={<SurfaceMark conversation={conversation} />}
          />
          <div className={cn(COLUMN, "flex-1 overflow-y-auto p-2xl")} data-testid="panel">
            {readable ? (
              <>
                <ConversationDetail
                  agent={agent}
                  conversation={conversation}
                  title={subject(conversation, viewer)}
                  onOpenArtifacts={() => onSelectSlot("artifacts")}
                />
                <p className="max-w-hint text-ink-soft">
                  This conversation is read-only here. Reply in {surfaceWord(conversation.surface)}{" "}
                  to continue it.
                </p>
              </>
            ) : (
              <Disclose
                agent={agent}
                conversation={conversation}
                onOpened={() => setDisclosed(true)}
              />
            )}
          </div>
        </div>
        {readable && slot ? (
          <ConversationSlotPane
            agent={agent}
            conversationId={conversation.id}
            slot={slot}
            embedded
            onClose={() => onSelectSlot(null)}
          />
        ) : null}
      </div>
    </Pane>
  );
}

function NotShared() {
  return <PaneNote>This conversation is not shared with this account.</PaneNote>;
}

function PaneNote({ children }: { children: React.ReactNode }) {
  return (
    <Pane className={COLUMN}>
      <div className="m-auto max-w-empty text-center text-ink-soft">{children}</div>
    </Pane>
  );
}

function NewChat({
  agents,
  mainAgent,
  collapsed,
  onExpand,
  onNewChat,
}: {
  agents: Agent[];
  mainAgent: Agent | null;
  collapsed: boolean;
  onExpand: () => void;
  onNewChat: (agentId: string) => void;
}) {
  const [picking, setPicking] = useState(false);
  const held = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!picking) return;
    const dismiss = (event: Event) => {
      if (event instanceof KeyboardEvent && event.key !== "Escape") return;
      if (event instanceof MouseEvent && held.current?.contains(event.target as Node)) return;
      setPicking(false);
    };
    document.addEventListener("keydown", dismiss);
    document.addEventListener("pointerdown", dismiss);
    return () => {
      document.removeEventListener("keydown", dismiss);
      document.removeEventListener("pointerdown", dismiss);
    };
  }, [picking]);

  if (!mainAgent) return null;
  if (agents.length === 1) {
    return (
      <NavRow icon={<NewChatGlyph />} current={false} collapsed={collapsed} label="New conversation" onClick={() => onNewChat(mainAgent.id)}>
        New conversation
      </NavRow>
    );
  }
  return (
    <div
      ref={held}
      className="flex flex-col gap-hair max-narrow:flex-row max-narrow:items-center"
    >
      <SidebarTooltip collapsed={collapsed} label="New conversation">
        <button
          type="button"
          aria-expanded={picking}
          aria-controls={PICKER_ID}
          onClick={() => {
            if (collapsed) onExpand();
            setPicking((open) => (collapsed ? true : !open));
          }}
          aria-label="New conversation"
          className={cn(NAV_ROW, collapsed && "justify-center gap-0 px-0 max-narrow:justify-start max-narrow:gap-sm max-narrow:px-sm", picking && "bg-fill")}
        >
          <NewChatGlyph />
          <span className={cn("min-w-0 flex-1 truncate", collapsed && "hidden max-narrow:inline")}>New conversation</span>
        </button>
      </SidebarTooltip>
      {picking ? (
        <ul id={PICKER_ID} className="m-0 flex list-none flex-col gap-hair p-0 max-narrow:flex-row">
          {agents.map((agent) => (
            <li key={agent.id}>
              <button
                type="button"
                onClick={() => {
                  setPicking(false);
                  onNewChat(agent.id);
                }}
                className="block w-full rounded-full border-0 bg-transparent py-xs pl-4xl pr-sm text-left text-label text-inherit hover:bg-fill max-narrow:w-auto max-narrow:whitespace-nowrap"
              >
                {agent.name}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function RailList({
  rail,
  route,
  mainAgent,
  sort,
  onSort,
  onOpen,
  onRetry,
}: {
  rail: Rail;
  route: Route;
  mainAgent: Agent | null;
  sort: RailSort;
  onSort: (sort: RailSort) => void;
  onOpen: (conversationId: string) => void;
  onRetry: () => void;
}) {
  const now = new Date();
  return (
    <>
      <div className="flex h-(--size-row) shrink-0 items-center justify-between pl-sm max-narrow:hidden">
        <h2 className="m-0 font-sans text-label font-medium text-ink-soft">Conversations</h2>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              aria-label="Conversation settings"
              className="rounded-control border-0 bg-transparent p-2xs text-ink-soft hover:bg-fill data-[state=open]:bg-fill"
            >
              <IconAdjustments className="size-(--size-glyph)" aria-hidden />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuSub>
              <DropdownMenuSubTrigger value={sort === "agent" ? "Agent" : "Recency"}>
                Sort by
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent>
                <DropdownMenuRadioGroup
                  value={sort}
                  onValueChange={(value) => onSort(value === "agent" ? "agent" : "recency")}
                >
                  <DropdownMenuRadioItem value="recency">Recency</DropdownMenuRadioItem>
                  <DropdownMenuRadioItem value="agent">Agent</DropdownMenuRadioItem>
                </DropdownMenuRadioGroup>
              </DropdownMenuSubContent>
            </DropdownMenuSub>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      {rail.phase === "loading" ? (
        <div className="p-sm text-ink-soft">Loading…</div>
      ) : null}
      {rail.phase === "failed" ? (
        <div className="flex flex-col gap-2xs p-sm text-ink-soft">
          <span>Couldn't load conversations.</span>
          <button
            type="button"
            onClick={onRetry}
            className="w-fit border-0 bg-transparent px-0 py-2xs text-left text-inherit underline"
          >
            Retry
          </button>
        </div>
      ) : null}
      {railGroups(rail.rows, sort, now).map((group) => (
        <section key={group.label} className="max-narrow:contents">
          <h2 className="m-0 flex h-(--size-row) items-center px-sm font-sans text-label font-medium text-ink-soft max-narrow:hidden">
            {group.label}
          </h2>
          <ul className="m-0 flex list-none flex-col gap-px p-0 max-narrow:flex-row">
            {group.rows.map((row) => {
              const facts = [
                row.speaker ? speakerName(row.speaker) : null,
                isPortalChat(row.surface) ? null : origin(row),
                mainAgent && row.agent_id !== mainAgent.id ? row.agent_name : null,
              ].filter((fact): fact is string => fact !== null);
              return (
                <li key={row.conversation_id}>
                  <RailRow
                    current={
                      (route.kind === "chat" || route.kind === "conversation-slot") &&
                      route.conversationId === row.conversation_id
                    }
                    facts={facts.length ? facts.join(" · ") : null}
                    onClick={() => onOpen(row.conversation_id)}
                  >
                    <SurfaceGlyph surface={row.surface} />
                    <span className="min-w-0 flex-1 truncate">{row.title}</span>
                  </RailRow>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </>
  );
}

const NAV_ROW =
  "flex h-(--size-row) w-full items-center gap-md rounded-full border-0 bg-transparent px-sm text-left text-label text-inherit hover:bg-fill max-narrow:w-auto max-narrow:whitespace-nowrap";

function SidebarTooltip({ collapsed, label, children }: { collapsed: boolean; label: string; children: React.ReactElement }) {
  if (!collapsed) return children;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}

function SidebarToggle({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button type="button" aria-label={label} onClick={onClick} className="rounded-control border-0 bg-transparent p-xs text-ink-soft hover:bg-fill max-narrow:hidden">
      <IconLayoutSidebarRight className="size-(--size-glyph)" aria-hidden />
    </button>
  );
}

/** The glyph states the palette the member picked, not the one the browser resolved: `System` is a
 *  choice they can read back, and a sun that flips itself at dusk would say they had picked light. */
function SchemeGlyph({ scheme }: { scheme: Scheme }) {
  if (scheme === "light") return <IconSun className="size-(--size-glyph)" aria-hidden />;
  if (scheme === "dark") return <IconMoon className="size-(--size-glyph)" aria-hidden />;
  return <IconDeviceDesktop className="size-(--size-glyph)" aria-hidden />;
}

function SchemePick({ collapsed }: { collapsed: boolean }) {
  const [scheme, setScheme] = useState<Scheme>(heldScheme);
  const pick = (next: Scheme) => {
    holdScheme(next);
    setScheme(next);
  };
  return (
    <DropdownMenu>
      <SidebarTooltip collapsed={collapsed} label="Theme">
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            aria-label="Theme"
            className="rounded-control border-0 bg-transparent p-2xs text-ink-soft hover:bg-fill data-[state=open]:bg-fill"
          >
            <SchemeGlyph scheme={scheme} />
          </button>
        </DropdownMenuTrigger>
      </SidebarTooltip>
      <DropdownMenuContent align="end">
        <DropdownMenuRadioGroup
          value={scheme}
          onValueChange={(value) => pick(value === "light" || value === "dark" ? value : "system")}
        >
          {SCHEME_OPTIONS.map((option) => (
            <DropdownMenuRadioItem key={option.scheme} value={option.scheme}>
              {option.label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function NavRow({
  icon,
  current,
  collapsed = false,
  label,
  onClick,
  children,
}: {
  icon: React.ReactNode;
  current: boolean;
  collapsed?: boolean;
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  const button = (
    <button
      type="button"
      aria-label={collapsed ? label : undefined}
      aria-current={current}
      onClick={onClick}
      className={cn(NAV_ROW, collapsed && "justify-center gap-0 px-0 max-narrow:justify-start max-narrow:gap-sm max-narrow:px-sm", current && "bg-fill")}
    >
      {icon}
      <span className={cn("min-w-0 flex-1 truncate", collapsed && "hidden max-narrow:inline")}>{children}</span>
    </button>
  );
  return <SidebarTooltip collapsed={collapsed} label={label}>{button}</SidebarTooltip>;
}

/** A fact the group above cannot state — the agent holding the conversation, the surface it came
 *  in on, whoever else spoke — is read on the way to a decision, not scanned. As a second line it
 *  doubles every row in the rail to serve the few that carry one, so it is held at the pointer and
 *  the rail keeps one pitch. A row with no such fact triggers nothing and draws no tooltip. The
 *  glyph is the exception: it costs the row no height, so the surface is scanned as well as read. */
function RailRow({
  current,
  facts,
  onClick,
  children,
}: {
  current: boolean;
  facts: string | null;
  onClick: () => void;
  children: React.ReactNode;
}) {
  const button = (
    <button
      type="button"
      aria-current={current}
      onClick={onClick}
      className={cn(
        "flex h-(--size-row) w-full items-center gap-xs rounded-full border-0 bg-transparent px-sm text-left text-label text-inherit hover:bg-fill",
        "max-narrow:w-auto max-narrow:whitespace-nowrap",
        current && "bg-fill",
      )}
    >
      {children}
    </button>
  );
  if (!facts) return button;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{button}</TooltipTrigger>
      <TooltipContent>{facts}</TooltipContent>
    </Tooltip>
  );
}

const SURFACE_GLYPH = "size-(--size-glyph) shrink-0 text-ink-soft";

/** The surface a conversation came in on, drawn before its title. The portal draws none: the rail
 *  is read in the portal, so a glyph on every row would state where the member already is. The
 *  words for the same fact stay in the row's tooltip, which is what a reader unable to see the
 *  glyph gets.
 *
 *  It is drawn at `--size-glyph`, the size every other mark in the sidebar takes, rather than at the
 *  row's own text size: a glyph scaled to a 13px label is read as a smudge beside a title that runs
 *  the width of the rail, and a mark nobody can name states no surface. */
function SurfaceGlyph({ surface }: { surface: string }) {
  if (surface === SLACK_SURFACE) return <IconBrandSlack className={SURFACE_GLYPH} aria-hidden />;
  if (surface === UFO_SURFACE) return <IconTerminal2 className={SURFACE_GLYPH} aria-hidden />;
  return null;
}

const SURFACE_MARK = "size-(--size-surface-mark) shrink-0";

function surfaceMark(surface: string): React.ReactNode {
  if (surface === SLACK_SURFACE) return <IconBrandSlack className={SURFACE_MARK} aria-hidden />;
  if (surface === UFO_SURFACE) return <IconTerminal2 className={SURFACE_MARK} aria-hidden />;
  return null;
}

/** The surface a conversation is happening on, at the head of the pane that reads it: the mark drawn
 *  larger than a rail row's glyph, and the room the surface named beside it. Where that surface
 *  reported where the conversation opened, the pair is the way out to it — drawn the way every act
 *  that leaves the portal is, keeping the line's own colour with no resting underline and the arrow
 *  muted beside the words. Where it reported none, the same pair states the fact and goes nowhere:
 *  a terminal session is not a place a link can land.
 *
 *  A surface with no mark of its own draws nothing at all. The header already names the agent, and
 *  a conversation read here is read-only whatever holds it, which the line under the transcript
 *  says in words. */
function SurfaceMark({ conversation }: { conversation: OwnedConversation }) {
  const mark = surfaceMark(conversation.surface);
  if (mark === null) return null;
  const where = origin(conversation);
  const href = slackLink(conversation.surface, conversation.source);
  if (href === null) {
    return (
      <span className="flex items-center gap-xs whitespace-nowrap text-ink-soft">
        {mark}
        {where}
      </span>
    );
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={"Open " + where + " in " + surfaceWord(conversation.surface)}
      className="flex items-center gap-xs whitespace-nowrap text-inherit no-underline hover:underline focus-visible:underline"
    >
      {mark}
      {where} <span className="text-ink-soft">↗</span>
    </a>
  );
}

const NewChatGlyph = () => <IconEdit className="size-(--size-glyph) shrink-0" aria-hidden />;
const AgentsGlyph = () => <IconSparkles className="size-(--size-glyph) shrink-0" aria-hidden />;
const RadarGlyph = () => <IconRadar className="size-(--size-glyph) shrink-0" aria-hidden />;
const ArtifactsGlyph = () => <IconFolder className="size-(--size-glyph) shrink-0" aria-hidden />;
const WorkspaceGlyph = () => <IconUsers className="size-(--size-glyph) shrink-0" aria-hidden />;
const SettingsGlyph = () => <IconSettings className="size-(--size-glyph) shrink-0" aria-hidden />;

const SECTION_GLYPHS: Record<Section, React.ReactNode> = {
  radar: <RadarGlyph />,
  artifacts: <ArtifactsGlyph />,
};
