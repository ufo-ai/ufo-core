import { createContext, useContext, type ReactNode } from "react";

import type { Agent } from "@/lib/types";

const MainAgent = createContext<Agent | null | undefined>(undefined);
const Audience = createContext<Agent[] | undefined>(undefined);

export function MainAgentProvider({ agents, children }: { agents: Agent[]; children: ReactNode }) {
  return (
    <Audience.Provider value={agents}>
      <MainAgent.Provider value={agents.find((agent) => agent.main) ?? null}>
        {children}
      </MainAgent.Provider>
    </Audience.Provider>
  );
}

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
