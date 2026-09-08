import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

type Drawer = {
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

export function useDrawerSlot(): (node: HTMLElement | null) => void {
  return useContext(DrawerContext).hold;
}

export function useDrawerHost(): HTMLElement | null {
  return useContext(DrawerContext).host;
}

export function useDrawerList(list: ReactNode): ReactNode {
  const { hosted, host } = useContext(DrawerContext);
  if (!hosted) return list;
  if (!host) return null;
  return createPortal(list, host);
}

export function useShutDrawer(): () => void {
  return useContext(DrawerContext).shut;
}
