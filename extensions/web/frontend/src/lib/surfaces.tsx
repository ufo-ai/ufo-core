import { createContext, useContext, type ReactNode } from "react";

import type { Surfaces } from "@/lib/types";

const Offered = createContext<Surfaces | undefined>(undefined);

/** Every screen offered. The boot read always states what this deploy offers, so this stands for
 *  the shell rendered by a caller that is not about the offer — a test of another screen. */
export const ALL_SURFACES: Surfaces = {
  admin: true,
  memory: true,
  "community-skills": true,
  "installed-skills": true,
  usage: true,
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

/** Which of the portal's own screens this deploy offers. A screen reads it to draw its entry —
 *  the tab, the gear — and never to decide what an address may open: the address stands whether
 *  or not the entry to it is drawn. */
export function useSurfaces(): Surfaces {
  const surfaces = useContext(Offered);
  if (surfaces === undefined) throw new Error("a view read the surfaces outside the provider");
  return surfaces;
}
