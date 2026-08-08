import type { ReactNode } from "react";

import { Listing, type ListingSpec } from "@/kernel/listing";
import type { Placement } from "@/kernel/pager";
import type { WorkspaceTab } from "@/lib/route";
import { ARTIFACTS } from "@/views/Artifacts";
import { Memory } from "@/views/Memory";
import { Sites } from "@/views/Sites";
import { SOURCES } from "@/views/Sources";
import { Team } from "@/views/Team";
import { CREDENTIALS } from "@/views/WorkspaceCredentials";
import { WorkspaceUsage } from "@/views/Usage";

export type WorkspaceView = {
  label: string;
  render: (place: Placement, onPlace: (place: Placement) => void) => ReactNode;
  /** Remount when the placement changes, so a mutation's outcome notice reaches the new mount. */
  remountOnPlace: boolean;
};

function declared<Payload, Row>(
  label: string,
  spec: ListingSpec<Payload, Row>,
): WorkspaceView {
  return {
    label,
    remountOnPlace: true,
    render: (place, onPlace) => (
      <Listing title={label} spec={spec} place={place} onPlace={onPlace} />
    ),
  };
}

export const WORKSPACE_VIEWS: Record<WorkspaceTab, WorkspaceView> = {
  team: {
    label: "Team",
    remountOnPlace: true,
    render: (place, onPlace) => <Team place={place} onPlace={onPlace} />,
  },
  sources: declared("Sources", SOURCES),
  credentials: declared("Credentials", CREDENTIALS),
  memory: {
    label: "Memory",
    remountOnPlace: false,
    render: (place, onPlace) => <Memory place={place} onPlace={onPlace} />,
  },
  artifacts: declared("Artifacts", ARTIFACTS),
  sites: { label: "Sites", remountOnPlace: false, render: () => <Sites /> },
  usage: {
    label: "Usage",
    remountOnPlace: false,
    render: () => <WorkspaceUsage />,
  },
};
