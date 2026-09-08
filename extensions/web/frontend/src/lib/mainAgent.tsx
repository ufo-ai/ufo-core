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

export function useAgents(): Agent[] {
  const agents = useContext(Audience);
  if (agents === undefined) throw new Error("a view read the audience outside the provider");
  return agents;
}

export const CHAT_SURFACE = "chat";

export function chatSurface(agents: Agent[]): Agent | null {
  return agents.find((agent) => agent.app === CHAT_SURFACE) ?? null;
}
