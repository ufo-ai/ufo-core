import { createContext, useContext, useMemo, type ReactNode } from "react";

import { usePanelRead } from "@/kernel/panel";
import type { Member, MemberFace } from "@/lib/types";

const ROSTER = "/workspace/team";

/** A member joining, or changing their picture, is not what a member reading a table is waiting on,
 *  so the roster re-reads far slower than the listings drawn over it. */
const ROSTER_MS = 300_000;

/** The face this workspace holds for an address, and null for an address it holds no member row
 *  for — a guest another organization shares a channel with, whose reported name is the whole
 *  answer. */
export type MemberFaces = (email: string | null | undefined) => MemberFace | null;

const Faces = createContext<MemberFaces | undefined>(undefined);

/** The roster, held once for the shell: a listing names a member by their address alone, and this
 *  is the one read that carries what that member looks like, so every table joins to it here
 *  rather than asking for a picture a row at a time. */
export function MemberFacesProvider({ children }: { children: ReactNode }) {
  const state = usePanelRead<{ members: Member[] }>(ROSTER, 0, ROSTER_MS);
  const members = state.phase === "ready" ? state.payload.members : undefined;
  const faces = useMemo<MemberFaces>(() => {
    const held = new Map((members ?? []).map((face) => [face.email.toLowerCase(), face]));
    return (email) => (email ? (held.get(email.toLowerCase()) ?? null) : null);
  }, [members]);
  return <Faces.Provider value={faces}>{children}</Faces.Provider>;
}

export function useMemberFaces(): MemberFaces {
  const faces = useContext(Faces);
  if (faces === undefined) throw new Error("a view read the member faces outside the provider");
  return faces;
}
