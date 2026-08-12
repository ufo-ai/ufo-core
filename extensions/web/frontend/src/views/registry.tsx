import type { ReactNode } from "react";

import { Listing, type ListingSpec } from "@/kernel/listing";
import type { Placement } from "@/kernel/pager";
import type { CustomizeTab, Section, WorkspaceTab } from "@/lib/route";
import { ARTIFACTS } from "@/views/Artifacts";
import { WorkspaceConnectors } from "@/views/Connectors";
import { Memory } from "@/views/Memory";
import { Scheduled } from "@/views/Scheduled";
import { Sites } from "@/views/Sites";
import { SOURCES } from "@/views/Sources";
import { Team } from "@/views/Team";
import { CREDENTIALS } from "@/views/WorkspaceCredentials";
import { WorkspaceUsage } from "@/views/Usage";

export type PaneView = {
  label: string;
  render: (place: Placement, onPlace: (place: Placement) => void) => ReactNode;
  /** Remount when the placement changes, so a mutation's outcome notice reaches the new mount. */
  remountOnPlace: boolean;
};

function declared<Payload, Row>(
  label: string,
  spec: ListingSpec<Payload, Row>,
): PaneView {
  return {
    label,
    remountOnPlace: true,
    render: (place, onPlace) => (
      <Listing title={label} spec={spec} place={place} onPlace={onPlace} />
    ),
  };
}

export const WORKSPACE_VIEWS: Record<WorkspaceTab, PaneView> = {
  team: {
    label: "Team",
    remountOnPlace: true,
    render: (place, onPlace) => <Team place={place} onPlace={onPlace} />,
  },
  sources: declared("Sources", SOURCES),
  connectors: {
    label: "Connectors",
    remountOnPlace: false,
    render: () => <WorkspaceConnectors />,
  },
  credentials: declared("Credentials", CREDENTIALS),
  usage: {
    label: "Usage",
    remountOnPlace: false,
    render: () => <WorkspaceUsage />,
  },
};

export const SECTION_VIEWS: Record<Section, PaneView> = {
  scheduled: { label: "Scheduled", remountOnPlace: false, render: () => <Scheduled /> },
  artifacts: declared("Artifacts", ARTIFACTS),
  sites: { label: "Sites", remountOnPlace: false, render: () => <Sites /> },
};

export const CUSTOMIZE_VIEWS: Record<CustomizeTab, PaneView> = {
  memory: {
    label: "Memory",
    remountOnPlace: false,
    render: (place, onPlace) => <Memory place={place} onPlace={onPlace} />,
  },
};
