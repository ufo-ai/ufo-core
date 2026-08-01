import { useCallback, useEffect, useState } from "react";

import { Admin } from "@/views/Admin";
import { AgentPane } from "@/views/AgentPane";
import { WORKSPACE_VIEWS } from "@/views/registry";
import { Workspace } from "@/views/Workspace";
import { MainAgentProvider } from "@/lib/mainAgent";
import { cn } from "@/lib/cn";
import {
  AGENT_TABS,
  WORKSPACE_TABS,
  agentHash,
  parseHash,
  workspaceHash,
  type AgentTab,
  type Route,
  type WorkspaceTab,
} from "@/lib/route";
import type { Agent, Member } from "@/lib/types";

export type AppProps = { agents: Agent[]; member: Member };

export function App({ agents, member }: AppProps) {
  const [route, setRoute] = useState<Route>(() => parseHash(location.hash));

  useEffect(() => {
    const onHash = () => setRoute(parseHash(location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const go = useCallback((hash: string, next: Route) => {
    if (location.hash !== hash) location.hash = hash;
    setRoute(next);
  }, []);

  const openAgent = useCallback(
    (agentId: string, tab: AgentTab = "chat") =>
      go(agentHash(agentId, tab), { kind: "agent", agentId, tab }),
    [go],
  );

  const openWorkspace = useCallback(
    (view: WorkspaceTab) => go(workspaceHash(view), { kind: "workspace", view }),
    [go],
  );

  const openAdmin = useCallback(() => go("#/admin", { kind: "admin" }), [go]);

  const selected =
    route.kind === "agent"
      ? agents.find((agent) => agent.id === route.agentId) ?? agents[0] ?? null
      : null;

  return (
    <MainAgentProvider agents={agents}>
    <div className="grid h-screen grid-cols-[var(--container-sidebar)_1fr] max-narrow:grid-cols-1 max-narrow:grid-rows-[auto_1fr]">
      <nav className="flex min-h-0 flex-col border-r border-edge max-narrow:flex-row max-narrow:items-center max-narrow:border-r-0 max-narrow:border-b">
        <div className="px-2xl py-lg font-strong max-narrow:px-lg max-narrow:py-md">
          ufo
        </div>
        <ul className="m-0 flex-1 list-none overflow-y-auto py-2xs max-narrow:flex max-narrow:overflow-y-hidden max-narrow:overflow-x-auto max-narrow:p-0">
          {agents.map((agent) => (
            <li key={agent.id} data-id={agent.id}>
              <SidebarButton
                current={selected !== null && selected.id === agent.id}
                onClick={() => openAgent(agent.id)}
              >
                <span>
                  {agent.name}
                  {agent.main ? " ·" : ""}
                </span>
                <span className="block font-mono text-mono opacity-(--muted-strong) max-narrow:hidden">
                  {agent.model}
                </span>
              </SidebarButton>
            </li>
          ))}
        </ul>
        <ul className="m-0 list-none border-t border-edge py-2xs max-narrow:flex max-narrow:overflow-y-hidden max-narrow:overflow-x-auto max-narrow:border-t-0 max-narrow:p-0">
          {WORKSPACE_TABS.map((name) => (
            <li key={name} data-tab={name}>
              <SidebarButton
                current={route.kind === "workspace" && route.view === name}
                onClick={() => openWorkspace(name)}
              >
                {WORKSPACE_VIEWS[name].label}
              </SidebarButton>
            </li>
          ))}
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
      {route.kind === "admin" ? (
        <Admin />
      ) : route.kind === "workspace" ? (
        <Workspace view={route.view} />
      ) : selected ? (
        <AgentPane
          agent={selected}
          tab={route.tab}
          tabs={AGENT_TABS}
          onTab={(tab) => openAgent(selected.id, tab)}
        />
      ) : null}
    </div>
    </MainAgentProvider>
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
