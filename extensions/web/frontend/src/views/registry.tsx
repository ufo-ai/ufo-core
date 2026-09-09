import { lazy, type ComponentType, type ReactNode } from "react";

import type { Placement } from "@/kernel/pager";
import type { Section, WorkspaceTab } from "@/lib/route";

export type PaneView = {
  label: string;
  render: (place: Placement, onPlace: (place: Placement) => void) => ReactNode;
  remountOnPlace: boolean;
  search?: string;
  ownsHeader?: boolean;
};

type Placed = { place: Placement; onPlace: (place: Placement) => void };

/** The bar draws every tab's label from this table, so the label is read at once and the pane behind it
 *  is a chunk the press pays for. */
function placed(load: () => Promise<{ default: ComponentType<Placed> }>) {
  const View = lazy(load);
  return (place: Placement, onPlace: (place: Placement) => void) => (
    <View place={place} onPlace={onPlace} />
  );
}

const WorkspaceMessaging = lazy(() =>
  import("@/views/Surfaces").then((module) => ({ default: module.WorkspaceMessaging })),
);

export const CONNECTORS: PaneView = {
  label: "Connectors",
  remountOnPlace: false,
  search: "Search connectors",
  render: placed(() =>
    import("@/views/Connectors").then((module) => ({ default: module.Connectors })),
  ),
};

export const WORKSPACE_VIEWS: Record<WorkspaceTab, PaneView> = {
  team: {
    label: "Team",
    remountOnPlace: true,
    search: "Search members",
    render: placed(() => import("@/views/Team").then((module) => ({ default: module.Team }))),
  },
  apps: {
    label: "Apps",
    remountOnPlace: false,
    render: placed(() => import("@/views/Apps").then((module) => ({ default: module.Apps }))),
  },
  tasks: {
    label: "Tasks",
    remountOnPlace: false,
    render: placed(() => import("@/views/Tasks").then((module) => ({ default: module.Tasks }))),
  },
  skills: {
    label: "Skills",
    remountOnPlace: false,
    search: "Search skills",
    render: placed(() =>
      import("@/views/WorkspaceSkills").then((module) => ({ default: module.WorkspaceSkills })),
    ),
  },
  memory: {
    label: "Memory",
    remountOnPlace: false,
    search: "Search memory",
    render: placed(() => import("@/views/Memory").then((module) => ({ default: module.Memory }))),
  },
  credentials: {
    label: "Credentials",
    remountOnPlace: true,
    search: "Search credentials",
    render: placed(() =>
      import("@/views/WorkspaceCredentials").then((module) => ({
        default: module.WorkspaceCredentials,
      })),
    ),
  },
  usage: {
    label: "Usage",
    remountOnPlace: false,
    render: placed(() =>
      import("@/views/Usage").then((module) => ({ default: module.WorkspaceUsage })),
    ),
  },
  billing: {
    label: "Billing",
    remountOnPlace: false,
    render: placed(() =>
      import("@/views/Billing").then((module) => ({ default: module.WorkspaceBilling })),
    ),
  },
};

export const MESSAGING: PaneView = {
  label: "Messaging",
  remountOnPlace: false,
  render: () => <WorkspaceMessaging />,
};

export const SECTION_VIEWS: Partial<Record<Section, PaneView>> = {
  connectors: CONNECTORS,
  messaging: MESSAGING,
};
