import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

/** The list a section keeps of its own records — the rail's conversations, the apps index — and
 *  where that list stands. At a desk width it stands beside the page, drawn where it is written. A
 *  phone screen has no width for a column beside the page, so the nav drawer takes the list: the
 *  same rows, doing the same work, drawn several components above the section that wrote them.
 *
 *  `host` is the element the drawer keeps for it, and it is null while the drawer is shut, because a
 *  shut drawer draws no contents at all. */
type Drawer = {
  /** Whether the drawer has taken the list. A list the drawer does not hold stands where it is. */
  hosted: boolean;
  host: HTMLElement | null;
  hold: (node: HTMLElement | null) => void;
  shut: () => void;
};

const DrawerContext = createContext<Drawer>({
  hosted: false,
  host: null,
  hold: () => {},
  shut: () => {},
});

export function DrawerHost({
  hosted,
  shut,
  children,
}: {
  hosted: boolean;
  shut: () => void;
  children: ReactNode;
}) {
  const [host, setHost] = useState<HTMLElement | null>(null);
  const slot = useMemo(() => ({ hosted, host, hold: setHost, shut }), [hosted, host, shut]);
  return <DrawerContext.Provider value={slot}>{children}</DrawerContext.Provider>;
}

/** The drawer's own hold on the element it keeps for the section's list. */
export function useDrawerSlot(): (node: HTMLElement | null) => void {
  return useContext(DrawerContext).hold;
}

/** The element the drawer holds the list in, for a layer the list raises over itself: a menu sent to
 *  the document's own end while the drawer stands is outside the drawer, which is a modal, so
 *  nothing reaches it there. Null wherever no drawer holds the list. */
export function useDrawerHost(): HTMLElement | null {
  return useContext(DrawerContext).host;
}

/** Where the section's list is drawn: in place beside the page, or in the drawer holding it. */
export function useDrawerList(list: ReactNode): ReactNode {
  const { hosted, host } = useContext(DrawerContext);
  if (!hosted) return list;
  if (!host) return null;
  return createPortal(list, host);
}

/** Shuts the drawer the list is standing in, for an act that draws something new on the page
 *  without navigating: a drawer left standing over that page states nothing about where the member
 *  is. Where no drawer holds the list it does nothing. */
export function useShutDrawer(): () => void {
  return useContext(DrawerContext).shut;
}
