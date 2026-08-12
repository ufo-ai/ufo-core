import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { SILENT, Toast, type ToastState } from "@/components/ui/toast";
import { Admin } from "@/views/Admin";
import { AgentPane } from "@/views/AgentPane";
import { Agents } from "@/views/Agents";
import { SubagentPane } from "@/views/SubagentPane";
import { ChatPane } from "@/views/ChatPane";
import { ConversationSlotPane } from "@/views/ConversationSlotPane";
import { ConversationDetail, Disclose } from "@/views/Conversations";
import { SignIn } from "@/views/SignIn";
import { TabbedPane } from "@/views/TabbedPane";
import { CUSTOMIZE_VIEWS, SECTION_VIEWS, WORKSPACE_VIEWS } from "@/views/registry";
import { Viewer, surfaceWord } from "@/lib/audience";
import { MainAgentProvider } from "@/lib/mainAgent";
import { getJson } from "@/lib/api";
import { cn } from "@/lib/cn";
import {
  bumpChat,
  groupChats,
  mergeChats,
  stampIso,
  type ChatRow,
  type ChatsPayload,
} from "@/lib/rail";
import {
  AGENT_TABS,
  CUSTOMIZE_TABS,
  SECTIONS,
  SUBAGENT_TABS,
  WORKSPACE_TABS,
  agentHash,
  artifactTarget,
  bootRoute,
  chatHash,
  customizeHash,
  newChatHash,
  parseHash,
  sectionHash,
  subagentHash,
  workspaceHash,
  type AgentTab,
  type CustomizeTab,
  type PlaceStep,
  type Route,
  type Section,
  type SubagentTab,
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
    (agentId: string, tab: AgentTab = "overview") =>
      go(agentHash(agentId, tab), { kind: "agent", agentId, tab }),
    [go],
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

  const openSubagent = useCallback(
    (name: string, tab: SubagentTab = "overview") =>
      go(subagentHash(name, tab), { kind: "subagent", name, tab }),
    [go],
  );

  const placeWorkspace = useCallback(
    (view: WorkspaceTab, place: WorkspacePlace, step: PlaceStep) => {
      const seen = routeRef.current;
      if (step !== "push" && (seen.kind !== "workspace" || seen.view !== view)) return;
      if (step === "back") {
        history.back();
        return;
      }
      const next: Route = { kind: "workspace", view, place };
      if (step === "replace") {
        history.replaceState(null, "", workspaceHash(view, place));
        setRoute(next);
        return;
      }
      go(workspaceHash(view, place), next);
    },
    [go],
  );

  const placeSection = useCallback(
    (section: Section, place: WorkspacePlace, step: PlaceStep) => {
      const seen = routeRef.current;
      if (step !== "push" && (seen.kind !== "section" || seen.section !== section)) return;
      if (step === "back") {
        history.back();
        return;
      }
      const next: Route = { kind: "section", section, place };
      if (step === "replace") {
        history.replaceState(null, "", sectionHash(section, place));
        setRoute(next);
        return;
      }
      go(sectionHash(section, place), next);
    },
    [go],
  );

  const placeCustomize = useCallback(
    (view: CustomizeTab, place: WorkspacePlace, step: PlaceStep) => {
      const seen = routeRef.current;
      if (step !== "push" && (seen.kind !== "customize" || seen.view !== view)) return;
      if (step === "back") {
        history.back();
        return;
      }
      const next: Route = { kind: "customize", view, place };
      if (step === "replace") {
        history.replaceState(null, "", customizeHash(view, place));
        setRoute(next);
        return;
      }
      go(customizeHash(view, place), next);
    },
    [go],
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
        origin: null,
      };
      setRail((current) => ({ ...current, rows: mergeChats(current.rows, [row]) }));
      const seen = routeRef.current;
      const origin =
        seen.kind === "home" || (seen.kind === "new-chat" && seen.agentId === agent.id);
      if (origin) openChat(conversationId);
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
      rail.rows.some((row) => row.conversation_id === wanted && !row.origin) ||
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
        <div className="grid h-dvh grid-cols-[var(--container-sidebar)_1fr] max-narrow:grid-cols-1 max-narrow:grid-rows-[auto_1fr]">
          <nav className="flex min-h-0 flex-col border-r border-edge max-narrow:flex-row max-narrow:items-center max-narrow:border-r-0 max-narrow:border-b">
            <div className="px-2xl py-lg font-strong max-narrow:px-lg max-narrow:py-md">ufo</div>
            <div className="px-lg pb-sm max-narrow:p-0">
              <NewChat agents={agents} mainAgent={mainAgent} onNewChat={openNewChat} />
            </div>
            <div className="flex-1 overflow-y-auto py-2xs max-narrow:flex max-narrow:items-center max-narrow:overflow-y-hidden max-narrow:overflow-x-auto max-narrow:p-0">
              <RailList
                rail={rail}
                route={route}
                mainAgent={mainAgent}
                onOpen={openChat}
                onRetry={() => setReloads((count) => count + 1)}
              />
            </div>
            <ul className="m-0 list-none border-t border-edge py-2xs max-narrow:flex max-narrow:overflow-y-hidden max-narrow:overflow-x-auto max-narrow:border-t-0 max-narrow:p-0">
              <li>
                <SidebarButton
                  current={
                    route.kind === "agents" || route.kind === "agent" || route.kind === "subagent"
                  }
                  onClick={openAgents}
                >
                  Agents
                </SidebarButton>
              </li>
              {SECTIONS.map((section) => (
                <li key={section}>
                  <SidebarButton
                    current={route.kind === "section" && route.section === section}
                    onClick={() => placeSection(section, {}, "push")}
                  >
                    {SECTION_VIEWS[section].label}
                  </SidebarButton>
                </li>
              ))}
              <li>
                <SidebarButton
                  current={route.kind === "customize"}
                  onClick={() => placeCustomize(CUSTOMIZE_TABS[0], {}, "push")}
                >
                  Customize
                </SidebarButton>
              </li>
              <li>
                <SidebarButton
                  current={route.kind === "workspace"}
                  onClick={() => placeWorkspace("team", {}, "push")}
                >
                  Workspace
                </SidebarButton>
              </li>
            </ul>
            <footer className="flex flex-col gap-2xs border-t border-edge px-2xl py-lg font-mono text-mono max-narrow:ml-auto max-narrow:border-t-0 max-narrow:px-lg max-narrow:py-md">
              <span className="max-narrow:hidden">
                {member.email}
                {member.admin ? " · admin" : ""}
              </span>
              {member.admin ? (
                <button
                  type="button"
                  onClick={openAdmin}
                  className="w-fit border-0 bg-transparent px-0 py-2xs text-left text-inherit underline"
                >
                  Administration
                </button>
              ) : null}
            </footer>
          </nav>
          <Pane
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
            onOpenSubagent={openSubagent}
            onNewChat={openNewChat}
            onPlaceWorkspace={placeWorkspace}
            onPlaceSection={placeSection}
            onPlaceCustomize={placeCustomize}
            sought={sought}
            linked={linked}
          />
          <Toast state={toast} onDone={() => setToast(SILENT)} />
        </div>
      </MainAgentProvider>
    </Viewer.Provider>
  );
}

function Pane({
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
  onOpenSubagent,
  onNewChat,
  onPlaceWorkspace,
  onPlaceSection,
  onPlaceCustomize,
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
  onOpenSubagent: (name: string, tab?: SubagentTab) => void;
  onNewChat: (agentId: string) => void;
  onPlaceWorkspace: (view: WorkspaceTab, place: WorkspacePlace, step: PlaceStep) => void;
  onPlaceSection: (section: Section, place: WorkspacePlace, step: PlaceStep) => void;
  onPlaceCustomize: (view: CustomizeTab, place: WorkspacePlace, step: PlaceStep) => void;
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
  if (route.kind === "customize") {
    return (
      <TabbedPane
        title="Customize"
        group="customize"
        tabs={CUSTOMIZE_TABS}
        views={CUSTOMIZE_VIEWS}
        view={route.view}
        place={route.place}
        onPlace={onPlaceCustomize}
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
  if (route.kind === "agents") {
    return (
      <Agents
        agents={agents}
        subagents={subagents}
        newAgent={newAgent}
        onOpen={onOpenAgent}
        onOpenSubagent={onOpenSubagent}
        onNewChat={onNewChat}
        onAgents={onAgents}
      />
    );
  }
  if (route.kind === "subagent") {
    const subagent = subagents.find((entry) => entry.name === route.name);
    if (!subagent) return <PaneNote>No such subagent.</PaneNote>;
    return (
      <SubagentPane
        subagent={subagent}
        tab={route.tab}
        tabs={SUBAGENT_TABS}
        onTab={(tab) => onOpenSubagent(subagent.name, tab)}
        conversationId={route.conversationId}
        rootConversationId={route.rootConversationId}
      />
    );
  }
  if (route.kind === "agent") {
    const agent = agents.find((entry) => entry.id === route.agentId);
    if (!agent) return <PaneNote>No such agent.</PaneNote>;
    return (
      <AgentPane
        agent={agent}
        tab={route.tab}
        tabs={AGENT_TABS}
        onTab={(tab) => onOpenAgent(agent.id, tab)}
      />
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
      (entry) => entry.conversation_id === route.conversationId && !entry.origin,
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
          onOpenAgent={onOpenAgent}
        />
      );
    }
    const agent = row ? agents.find((entry) => entry.id === row.agent_id) : undefined;
    if (!row || !agent) {
      if (rail.phase === "loading") return <PaneNote>Loading…</PaneNote>;
      if (rail.phase === "failed") return <PaneNote>Couldn't load conversations.</PaneNote>;
      const outcome = sought[route.conversationId];
      if (!outcome) return <PaneNote>Loading…</PaneNote>;
      if (outcome.kind === "signed-out") {
        return (
          <main className="flex min-h-0 min-w-0 flex-col">
            <SignIn />
          </main>
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
        onOpenAgent={onOpenAgent}
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
      onOpenAgent={onOpenAgent}
    />
  );
}

function LinkedPane({
  agent,
  conversation,
  onOpenAgent,
}: {
  agent: Agent;
  conversation: OwnedConversation;
  onOpenAgent: (agentId: string, tab?: AgentTab) => void;
}) {
  const [disclosed, setDisclosed] = useState(false);
  const back = () => onOpenAgent(agent.id, "conversations");
  return (
    <main className="flex min-h-0 min-w-0 flex-col">
      <div className="flex-1 overflow-y-auto p-2xl" data-testid="panel">
        {conversation.readable || disclosed ? (
          <>
            <ConversationDetail agent={agent} conversation={conversation} onBack={back} />
            <p className="max-w-hint opacity-(--muted-soft)">
              This conversation is read-only here. Reply in {surfaceWord(conversation.surface)} to
              continue it.
            </p>
          </>
        ) : (
          <Disclose
            agent={agent}
            conversation={conversation}
            onBack={back}
            onOpened={() => setDisclosed(true)}
          />
        )}
      </div>
    </main>
  );
}

function NotShared() {
  return <PaneNote>This conversation is not shared with this account.</PaneNote>;
}

function PaneNote({ children }: { children: React.ReactNode }) {
  return (
    <main className="flex min-h-0 min-w-0 flex-col">
      <div className="m-auto max-w-empty text-center opacity-(--muted-soft)">{children}</div>
    </main>
  );
}

function NewChat({
  agents,
  mainAgent,
  onNewChat,
}: {
  agents: Agent[];
  mainAgent: Agent | null;
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
      <Button
        onClick={() => onNewChat(mainAgent.id)}
        className="w-full text-left max-narrow:w-auto max-narrow:whitespace-nowrap"
      >
        New conversation
      </Button>
    );
  }
  return (
    <div
      ref={held}
      className="flex flex-col gap-2xs max-narrow:flex-row max-narrow:items-center"
    >
      <Button
        aria-expanded={picking}
        aria-controls={PICKER_ID}
        onClick={() => setPicking((open) => !open)}
        className="w-full text-left max-narrow:w-auto max-narrow:whitespace-nowrap"
      >
        New conversation
      </Button>
      {picking ? (
        <ul id={PICKER_ID} className="m-0 flex list-none flex-col p-0 max-narrow:flex-row">
          {agents.map((agent) => (
            <li key={agent.id}>
              <button
                type="button"
                onClick={() => {
                  setPicking(false);
                  onNewChat(agent.id);
                }}
                className="block w-full border-0 bg-transparent py-xs pl-xl pr-lg text-left text-inherit hover:bg-fill-hover max-narrow:w-auto max-narrow:whitespace-nowrap"
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
  onOpen,
  onRetry,
}: {
  rail: Rail;
  route: Route;
  mainAgent: Agent | null;
  onOpen: (conversationId: string) => void;
  onRetry: () => void;
}) {
  const now = new Date();
  return (
    <>
      {rail.phase === "loading" ? (
        <div className="px-2xl py-sm opacity-(--muted)">Loading…</div>
      ) : null}
      {rail.phase === "failed" ? (
        <div className="flex flex-col gap-2xs px-2xl py-sm opacity-(--muted)">
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
      {groupChats(rail.rows, now).map((group) => (
        <section key={group.label} className="max-narrow:contents">
          <h2 className="m-0 px-2xl pb-2xs pt-lg text-small opacity-(--muted-strong) max-narrow:hidden">
            {group.label}
          </h2>
          <ul className="m-0 list-none p-0 max-narrow:flex">
            {group.rows.map((row) => (
              <li key={row.conversation_id}>
                <SidebarButton
                  current={
                    (route.kind === "chat" || route.kind === "conversation-slot") &&
                    route.conversationId === row.conversation_id
                  }
                  onClick={() => onOpen(row.conversation_id)}
                >
                  <span className="block truncate">{row.title}</span>
                  {row.origin || (mainAgent && row.agent_id !== mainAgent.id) ? (
                    <span className="block truncate text-small opacity-(--muted-strong) max-narrow:hidden">
                      {[row.origin, mainAgent && row.agent_id !== mainAgent.id ? row.agent_name : null]
                        .filter((fact): fact is string => fact !== null)
                        .join(" · ")}
                    </span>
                  ) : null}
                </SidebarButton>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </>
  );
}

function SidebarButton({
  current,
  onClick,
  children,
}: {
  current: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-current={current}
      onClick={onClick}
      className={cn(
        "block w-full border-0 border-l-(length:--marker-width) border-l-transparent bg-transparent",
        "py-sm pl-xl pr-2xl text-left text-inherit hover:bg-fill-hover",
        "max-narrow:w-auto max-narrow:whitespace-nowrap max-narrow:border-l-0",
        "max-narrow:border-b-(length:--marker-width) max-narrow:border-b-transparent",
        current &&
          "border-l-ink bg-fill-subtle max-narrow:border-b-ink max-narrow:border-l-transparent",
      )}
    >
      {children}
    </button>
  );
}
