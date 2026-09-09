import { createContext, useContext, type ReactNode } from "react";

import type { Agent, ArchivedApp } from "@/lib/types";

type AppsState = {
  agents: Agent[];
  archived: ArchivedApp[];
  onRestored: () => void;
};

const AppsContext = createContext<AppsState | null>(null);

export function useApps(): AppsState {
  const state = useContext(AppsContext);
  if (!state) throw new Error("AppsProvider is required");
  return state;
}

export function AppsProvider({
  agents,
  archived,
  onRestored,
  children,
}: AppsState & { children: ReactNode }) {
  return (
    <AppsContext.Provider value={{ agents, archived, onRestored }}>
      {children}
    </AppsContext.Provider>
  );
}
