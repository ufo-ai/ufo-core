import { lazy, type ComponentType, type ReactNode } from "react";

import type { Placement } from "@/kernel/pager";
import type { ConnectionTab, Section, WorkspaceTab } from "@/lib/route";

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

export const AUTOMATIONS_TAB = "automations";

export const AUTOMATIONS_VIEWS: Record<typeof AUTOMATIONS_TAB, PaneView> = {
  automations: {
    label: "Automations",
    remountOnPlace: false,
    search: "Search automations",
    render: placed(() =>
      import("@/views/Automations").then((module) => ({ default: module.Automations })),
    ),
  },
};

export const RADAR: PaneView = {
  label: "Radar",
  remountOnPlace: false,
  ownsHeader: true,
  render: placed(() => import("@/views/Radar").then((module) => ({ default: module.Radar }))),
};

export const ARTIFACTS: PaneView = {
  label: "Artifacts",
  remountOnPlace: false,
  search: "Search artifacts",
  render: placed(() =>
    import("@/views/Artifacts").then((module) => ({ default: module.Artifacts })),
  ),
};

export const CONNECTORS: PaneView = {
  label: "Connections",
  remountOnPlace: false,
  search: "Search connections",
  render: placed(() =>
    import("@/views/Connectors").then((module) => ({ default: module.Connectors })),
  ),
};

export const CONNECTION_VIEWS: Record<ConnectionTab, PaneView> = {
  connectors: CONNECTORS,
};

export const WORKSPACE_VIEWS: Record<WorkspaceTab, PaneView> = {
  profile: {
    label: "Profile",
    remountOnPlace: false,
    render: placed(() =>
      import("@/views/Profile").then((module) => ({ default: module.Profile })),
    ),
  },
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
  chat: {
    label: "Chat defaults",
    remountOnPlace: false,
    render: placed(() =>
      import("@/views/ChatDefaults").then((module) => ({ default: module.ChatDefaults })),
    ),
  },
  skills: {
    label: "Skills",
    remountOnPlace: false,
    search: "Search skills",
    render: placed(() =>
      import("@/views/WorkspaceSkills").then((module) => ({ default: module.WorkspaceSkills })),
    ),
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
  memory: {
    label: "Memory",
    remountOnPlace: false,
    search: "Search memory",
    render: placed(() => import("@/views/Memory").then((module) => ({ default: module.Memory }))),
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
  email: {
    label: "Notifications",
    remountOnPlace: false,
    render: placed(() =>
      import("@/views/WorkspaceNotifications").then((module) => ({ default: module.WorkspaceNotifications })),
    ),
  },
};

export const SECTION_VIEWS: Partial<Record<Section, PaneView>> = {
  radar: RADAR,
  artifacts: ARTIFACTS,
  ...CONNECTION_VIEWS,
};
