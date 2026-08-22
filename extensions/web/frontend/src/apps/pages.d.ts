import type * as Kit from "./kit";

declare global {
  const UfoAppKit: typeof Kit;
  type Agent = Kit.Agent;
  type AppInit = Kit.AppInit;
  type ChatRow = Kit.ChatRow;
  type Face = Kit.Face;
  type FacetGroup = Kit.FacetGroup;
  type Member = Kit.Member;
  type ObjectAddress = Kit.ObjectAddress;
  type ObjectRow = Kit.ObjectRow;
  type PaneView = Kit.PaneView;
  type PanelState<T> = Kit.PanelState<T>;
  type Placement = Kit.Placement;
  type ReactNode = import("react").ReactNode;
  type RefObject<T> = import("react").RefObject<T>;
  type ReactMouseEvent<T = Element> = import("react").MouseEvent<T>;
}

export {};
