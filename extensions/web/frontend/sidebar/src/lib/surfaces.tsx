import { createContext, useContext, type ReactNode } from "react";

import { WORKSPACE_TABS, type WorkspaceTab } from "@/lib/route";
import type { Surfaces } from "@/lib/types";

const Offered = createContext<Surfaces | undefined>(undefined);

/** Every screen offered. The boot read always states what this deploy offers, so this stands for
 *  the shell rendered by a caller that is not about the offer — a test of another screen. */
export const ALL_SURFACES: Surfaces = {
  team: true,
  memory: true,
  "community-skills": true,
  "installed-skills": true,
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

/** Which of the portal's own screens this member is offered. A screen reads it to draw its entry —
 *  the tab, the nav row — and never to decide what an address may open: the address stands whether
 *  or not the entry to it is drawn. */
export function useSurfaces(): Surfaces {
  const surfaces = useContext(Offered);
  if (surfaces === undefined) throw new Error("a view read the surfaces outside the provider");
  return surfaces;
}

/** The workspace tabs this member is drawn, in the order the destination lists them. A withheld
 *  screen loses its tab and keeps its address, so a member holding the link still lands on it; the
 *  skills tab stands while either of its two panels does, and goes when neither is offered. The
 *  first of them is where the destination opens, so no entry to it — a nav row, a palette row —
 *  lands a member on a tab they are not drawn. */
export function useOfferedTabs(): readonly WorkspaceTab[] {
  const surfaces = useSurfaces();
  return WORKSPACE_TABS.filter((tab) =>
    tab === "team"
      ? surfaces.team
      : tab === "memory"
        ? surfaces.memory
        : tab === "skills"
          ? surfaces["community-skills"] || surfaces["installed-skills"]
          : true,
  );
}
