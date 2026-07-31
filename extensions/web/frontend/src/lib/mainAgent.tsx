import { createContext, useContext, type ReactNode } from "react";

import type { Agent } from "@/lib/types";

const MainAgent = createContext<Agent | null | undefined>(undefined);

export function MainAgentProvider({ agents, children }: { agents: Agent[]; children: ReactNode }) {
  return (
    <MainAgent.Provider value={agents.find((agent) => agent.main) ?? null}>
      {children}
    </MainAgent.Provider>
  );
}

export function useMainAgent(): Agent | null {
  const agent = useContext(MainAgent);
  if (agent === undefined) throw new Error("a view read the main agent outside the provider");
  return agent;
}
