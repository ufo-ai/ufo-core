import { createContext, useContext, type ReactNode } from "react";

import { WORKSPACE_TABS, type WorkspaceTab } from "@/lib/route";
import type { Surfaces } from "@/lib/types";

const Offered = createContext<Surfaces | undefined>(undefined);

export const ALL_SURFACES: Surfaces = {
  team: true,
  apps: true,
  memory: true,
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
  if (surfaces === undefined) throw new Error("a view read the surfaces outside the provider");
  return surfaces;
}

export function useOfferedTabs(): readonly WorkspaceTab[] {
  const surfaces = useSurfaces();
  return WORKSPACE_TABS.filter((tab) =>
    tab === "team"
      ? surfaces.team
      : tab === "apps"
        ? surfaces.apps
        : tab === "memory"
          ? surfaces.memory
          : tab === "skills"
            ? surfaces["community-skills"] || surfaces["installed-skills"]
            : true,
  );
}
