import type { ReactNode } from "react";

import { Listing, type ListingSpec } from "@/kernel/listing";
import type { Placement } from "@/kernel/pager";
import type { Section, WorkspaceTab } from "@/lib/route";
import { Artifacts } from "@/views/Artifacts";
import { Automations } from "@/views/Automations";
import { WorkspaceConnectors } from "@/views/Connectors";
import { Memory } from "@/views/Memory";
import { SOURCES } from "@/views/Sources";
import { Team } from "@/views/Team";
import { CREDENTIALS } from "@/views/WorkspaceCredentials";
import { WorkspaceUsage } from "@/views/Usage";

export type PaneView = {
  label: string;
  render: (place: Placement, onPlace: (place: Placement) => void) => ReactNode;
  /** Remount when the placement changes, so a mutation's outcome notice reaches the new mount. */
  remountOnPlace: boolean;
  /** The placeholder of the search the page's own header carries, for a view that narrows on `q`.
   *  The header owns the box because it outlives the read the view redraws under it. */
  search?: string;
  /** A view that heads its own page — the shell draws no header over it, because the controls
   *  reaching the whole page are the view's own and stand beside its title. */
  ownsHeader?: boolean;
};

function declared<Payload, Row>(label: string, spec: ListingSpec<Payload, Row>): PaneView {
  return {
    label,
    remountOnPlace: true,
    render: (place, onPlace) => (
      <Listing spec={spec} place={place} onPlace={onPlace} />
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
  automations: {
    label: "Automations",
    remountOnPlace: false,
    ownsHeader: true,
    render: () => <Automations agentId={null} title="Automations" />,
  },
  memory: {
    label: "Memory",
    remountOnPlace: false,
    search: "Search",
    render: (place, onPlace) => <Memory place={place} onPlace={onPlace} />,
  },
  artifacts: {
    label: "Artifacts",
    remountOnPlace: false,
    search: "Search",
    render: (place, onPlace) => <Artifacts place={place} onPlace={onPlace} />,
  },
};

