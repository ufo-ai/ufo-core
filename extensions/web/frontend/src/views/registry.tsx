import type { ReactNode } from "react";

import { Listing, type ListingSpec } from "@/kernel/listing";
import type { Placement } from "@/kernel/pager";
import type { Section, WorkspaceTab } from "@/lib/route";
import { Connectors } from "@/views/Connectors";
import { WorkspaceMessaging } from "@/views/Surfaces";
import { Apps } from "@/views/Apps";
import { Memory } from "@/views/Memory";
import { Tasks } from "@/views/Tasks";
import { Team } from "@/views/Team";
import { WorkspaceSkills } from "@/views/WorkspaceSkills";
import { CREDENTIALS } from "@/views/WorkspaceCredentials";
import { WorkspaceBilling } from "@/views/Billing";
import { WorkspaceUsage } from "@/views/Usage";

export type PaneView = {
  label: string;
  render: (place: Placement, onPlace: (place: Placement) => void) => ReactNode;
  remountOnPlace: boolean;
  search?: string;
  ownsHeader?: boolean;
};

function declared<Payload, Row>(label: string, spec: ListingSpec<Payload, Row>): PaneView {
  return {
    label,
    remountOnPlace: true,
    search: spec.search || spec.serverQuery ? "Search " + label.toLowerCase() : undefined,
    render: (place, onPlace) => (
      <Listing spec={spec} place={place} onPlace={onPlace} />
    ),
  };
}

export const CONNECTORS: PaneView = {
  label: "Connectors",
  remountOnPlace: false,
  search: "Search connectors",
  render: (place, onPlace) => <Connectors place={place} onPlace={onPlace} />,
};

export const WORKSPACE_VIEWS: Record<WorkspaceTab, PaneView> = {
  team: {
    label: "Team",
    remountOnPlace: true,
    search: "Search members",
    render: (place, onPlace) => <Team place={place} onPlace={onPlace} />,
  },
  apps: {
    label: "Apps",
    remountOnPlace: false,
    render: (place, onPlace) => <Apps place={place} onPlace={onPlace} />,
  },
  tasks: {
    label: "Tasks",
    remountOnPlace: false,
    render: (place, onPlace) => <Tasks place={place} onPlace={onPlace} />,
  },
  skills: {
    label: "Skills",
    remountOnPlace: false,
    search: "Search skills",
    render: (place, onPlace) => <WorkspaceSkills place={place} onPlace={onPlace} />,
  },
  memory: {
    label: "Memory",
    remountOnPlace: false,
    search: "Search memory",
    render: (place, onPlace) => <Memory place={place} onPlace={onPlace} />,
  },
  credentials: declared("Credentials", CREDENTIALS),
  usage: {
    label: "Usage",
    remountOnPlace: false,
    render: (place, onPlace) => <WorkspaceUsage place={place} onPlace={onPlace} />,
  },
  billing: {
    label: "Billing",
    remountOnPlace: false,
    render: (place, onPlace) => <WorkspaceBilling place={place} onPlace={onPlace} />,
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
