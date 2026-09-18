import { createContext, useContext, type ReactNode } from "react";

import { useMe } from "@/lib/audience";
import { WORKSPACE_TABS, type WorkspaceTab } from "@/lib/route";
import type { Surfaces } from "@/lib/types";

const Offered = createContext<Surfaces | undefined>(undefined);

export const ALL_SURFACES: Surfaces = {
  team: true,
  apps: true,
  email: true,
  memory: true,
  radar: true,
  "community-skills": true,
  "installed-skills": true,
  "app-store": true,
};

export function SurfacesProvider({
  surfaces,
  children,
}: {
  surfaces: Surfaces;
  children: ReactNode;
}) {
  return <Offered.Provider value={surfaces}>{children}</Offered.Provider>;
}

export function useSurfaces(): Surfaces {
  const surfaces = useContext(Offered);
  if (surfaces === undefined)
    throw new Error("a view read the surfaces outside the provider");
  return surfaces;
}

export function useOfferedTabs(): readonly WorkspaceTab[] {
  const surfaces = useSurfaces();
  const member = useMe();
  return WORKSPACE_TABS.filter((tab) => {
    switch (tab) {
      case "admin": return member?.admin === true;
      case "team": return surfaces.team;
      case "email": return surfaces.email;
      case "apps": return surfaces.apps;
      case "memory": return surfaces.memory;
      case "skills": return surfaces["community-skills"] || surfaces["installed-skills"];
      default: return true;
    }
  });
}
