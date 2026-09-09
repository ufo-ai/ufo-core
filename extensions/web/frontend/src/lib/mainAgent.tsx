import { createContext, useContext, type ReactNode } from "react";

import type { Agent } from "@/lib/types";

const MainAgent = createContext<Agent | null | undefined>(undefined);
const Audience = createContext<Agent[] | undefined>(undefined);
const Reread = createContext<(() => void) | null>(null);

export function MainAgentProvider({
  agents,
  onAgents,
  children,
}: {
  agents: Agent[];
  onAgents?: () => void;
  children: ReactNode;
}) {
  return (
    <Reread.Provider value={onAgents ?? null}>
      <Audience.Provider value={agents}>
        <MainAgent.Provider value={agents.find((agent) => agent.main) ?? null}>
          {children}
        </MainAgent.Provider>
      </Audience.Provider>
    </Reread.Provider>
  );
}

/** The workspace's main agent, or null before the roster arrives. */
export function useMainAgent(): Agent | null {
  return useAgents().find((agent) => agent.main) ?? null;
}

/** Every agent the viewer's web audience holds. A view listing one kind across the workspace names
 *  the agent an act lands on, which the main agent alone cannot answer. */
export function useAgents(): Agent[] {
  const agents = useContext(Audience);
  if (agents === undefined) throw new Error("a view read the audience outside the provider");
  return agents;
}

/** Read the roster again after a write to an agent row, so every screen holding that row draws the
 *  written value rather than the one the boot read carried. Null where the shell took no roster
 *  read of its own to refresh. */
export function useRereadAgents(): (() => void) | null {
  return useContext(Reread);
}

export const CHAT_SURFACE = "chat";

export function chatSurface(agents: Agent[]): Agent | null {
  return agents.find((agent) => agent.app === CHAT_SURFACE) ?? null;
}
