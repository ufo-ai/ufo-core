import { useCallback, useEffect, useRef, useState } from "react";

import { Admin } from "@/views/Admin";
import { AgentPane } from "@/views/AgentPane";
import { Agents } from "@/views/Agents";
import { SubagentPane } from "@/views/SubagentPane";
import { ChatPane } from "@/views/ChatPane";
import { ChangesPane } from "@/views/ChangesPane";
import { Workspace } from "@/views/Workspace";
import { MainAgentProvider } from "@/lib/mainAgent";
import { getJson } from "@/lib/api";
import { cn } from "@/lib/cn";
import {
  bumpChat,
  groupChats,
  mergeChats,
  relativeTime,
  stampIso,
  type ChatRow,
  type ChatsPayload,
} from "@/lib/rail";
import {
  AGENT_TABS,
  SUBAGENT_TABS,
  agentHash,
  chatHash,
  changesHash,
  newChatHash,
  parseHash,
  subagentHash,
  workspaceHash,
  type AgentTab,
  type PlaceStep,
  type Route,
  type SubagentTab,
  type WorkspacePlace,
  type WorkspaceTab,
} from "@/lib/route";
import type { Agent, Member, Subagent } from "@/lib/types";

export type AppProps = { agents: Agent[]; subagents: Subagent[]; member: Member };

type Rail = { phase: "loading" | "failed" | "ready"; rows: ChatRow[] };

export function App({ agents, subagents, member }: AppProps) {
  const [route, setRoute] = useState<Route>(() => parseHash(location.hash));
  const [rail, setRail] = useState<Rail>({ phase: "loading", rows: [] });
  const [reloads, setReloads] = useState(0);
  const [sought, setSought] = useState<readonly string[]>([]);
  const mainAgent = agents.find((agent) => agent.main) ?? agents[0] ?? null;
  const routeRef = useRef(route);
  routeRef.current = route;

  useEffect(() => {
    const onHash = () => setRoute(parseHash(location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    let live = true;
    setRail((current) => ({ phase: "loading", rows: current.rows }));
    getJson<ChatsPayload>("/api/chats").then((result) => {
      if (!live) return;
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

  const openChanges = useCallback(
    (agentId: string, conversationId: string) =>
      go(changesHash(agentId, conversationId), { kind: "changes", agentId, conversationId }),
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

  const openAdmin = useCallback(() => go("#/admin", { kind: "admin" }), [go]);

  const created = useCallback(
    (agent: Agent, conversationId: string, title: string) => {
      const row: ChatRow = {
        conversation_id: conversationId,
        agent_id: agent.id,
        agent_name: agent.name,
        title,
        last_at: stampIso(new Date()),
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
    if (rail.rows.some((row) => row.conversation_id === wanted) || sought.includes(wanted)) {
      return;
    }
    let live = true;
    getJson<ChatsPayload>("/api/chats?conversation=" + wanted).then((result) => {
      if (!live) return;
      setSought((current) => current.concat(wanted));
      if (result.ok && result.payload.chats.length) {
        setRail((current) => ({ ...current, rows: mergeChats(current.rows, result.payload.chats) }));
      }
    });
    return () => {
      live = false;
    };
  }, [route, rail, sought]);

  return (
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
                className="border-0 bg-transparent p-0 text-left text-inherit underline"
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
          mainAgent={mainAgent}
          rail={rail}
          onCreated={created}
          onActivity={activity}
          onOpenAgent={openAgent}
          onOpenChanges={openChanges}
          onOpenSubagent={openSubagent}
          onNewChat={openNewChat}
          onPlaceWorkspace={placeWorkspace}
          sought={sought}
        />
      </div>
    </MainAgentProvider>
  );
}

function Pane({
  route,
  agents,
  subagents,
  member,
  mainAgent,
  rail,
  onCreated,
  onActivity,
  onOpenAgent,
  onOpenChanges,
  onOpenSubagent,
  onNewChat,
  onPlaceWorkspace,
  sought,
}: {
  route: Route;
  agents: Agent[];
  subagents: Subagent[];
  member: Member;
  mainAgent: Agent | null;
  rail: Rail;
  onCreated: (agent: Agent, conversationId: string, title: string) => void;
  onActivity: (conversationId: string) => void;
  onOpenAgent: (agentId: string, tab?: AgentTab) => void;
  onOpenChanges: (agentId: string, conversationId: string) => void;
  onOpenSubagent: (name: string, tab?: SubagentTab) => void;
  onNewChat: (agentId: string) => void;
  onPlaceWorkspace: (view: WorkspaceTab, place: WorkspacePlace, step: PlaceStep) => void;
  sought: readonly string[];
}) {
  if (route.kind === "admin") return <Admin />;
  if (route.kind === "workspace") {
    return <Workspace view={route.view} place={route.place} onPlace={onPlaceWorkspace} />;
  }
  if (route.kind === "agents") {
    return (
      <Agents
        agents={agents}
        subagents={subagents}
        onOpen={onOpenAgent}
        onOpenSubagent={onOpenSubagent}
        onNewChat={onNewChat}
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
        onNewChat={onNewChat}
      />
    );
  }
  if (route.kind === "changes") {
    const agent = agents.find((entry) => entry.id === route.agentId);
    if (!agent) return <PaneNote>No such agent.</PaneNote>;
    return (
      <ChangesPane
        agent={agent}
        conversationId={route.conversationId}
        rootConversationId={route.rootConversationId}
        onOpenAgent={onOpenAgent}
      />
    );
  }
  if (route.kind === "chat") {
    const row = rail.rows.find((entry) => entry.conversation_id === route.conversationId);
    const agent = row ? agents.find((entry) => entry.id === row.agent_id) : undefined;
    if (!row || !agent) {
      if (rail.phase === "loading") return <PaneNote>Loading…</PaneNote>;
      if (rail.phase === "failed") return <PaneNote>Couldn't load conversations.</PaneNote>;
      if (!sought.includes(route.conversationId)) return <PaneNote>Loading…</PaneNote>;
      return <PaneNote>No such conversation.</PaneNote>;
    }
    return (
      <ChatPane
        key={row.conversation_id}
        agent={agent}
        member={member}
        conversationId={row.conversation_id}
        onActivity={onActivity}
        onOpenAgent={onOpenAgent}
        onOpenChanges={onOpenChanges}
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
  if (!mainAgent) return null;
  if (agents.length === 1) {
    return (
      <button
        type="button"
        onClick={() => onNewChat(mainAgent.id)}
        className="w-full rounded-control border border-edge-control bg-transparent px-lg py-xs text-left text-inherit hover:bg-fill-hover max-narrow:w-auto max-narrow:whitespace-nowrap"
      >
        New conversation
      </button>
    );
  }
  return (
    <div className="flex flex-col gap-2xs max-narrow:flex-row max-narrow:items-center">
      <button
        type="button"
        aria-expanded={picking}
        onClick={() => setPicking((open) => !open)}
        className="w-full rounded-control border border-edge-control bg-transparent px-lg py-xs text-left text-inherit hover:bg-fill-hover max-narrow:w-auto max-narrow:whitespace-nowrap"
      >
        New conversation
      </button>
      {picking ? (
        <ul className="m-0 flex list-none flex-col p-0 max-narrow:flex-row">
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
            className="border-0 bg-transparent p-0 text-left text-inherit underline"
          >
            Retry
          </button>
        </div>
      ) : null}
      {groupChats(rail.rows, now).map((group) => (
        <section key={group.label} className="max-narrow:contents">
          <h2 className="m-0 px-2xl pb-2xs pt-md text-small font-strong opacity-(--muted) max-narrow:hidden">
            {group.label}
          </h2>
          <ul className="m-0 list-none p-0 max-narrow:flex">
            {group.rows.map((row) => (
              <li key={row.conversation_id}>
                <SidebarButton
                  current={
                    (route.kind === "chat" || route.kind === "changes") &&
                    route.conversationId === row.conversation_id
                  }
                  onClick={() => onOpen(row.conversation_id)}
                >
                  <span className="block overflow-hidden text-ellipsis whitespace-nowrap">
                    {row.title}
                  </span>
                  <span className="block font-mono text-mono opacity-(--muted-strong) max-narrow:hidden">
                    {mainAgent && row.agent_id !== mainAgent.id ? row.agent_name + " · " : ""}
                    {relativeTime(row.last_at, now)}
                  </span>
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
