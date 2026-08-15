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
  /** What the search the page's own header carries is labelled — the words for what it narrows,
   *  never the bare verb, since one shell heads several tabs and a box called `Search` on every one
   *  of them names none. The header owns the box because it outlives the read it redraws under. */
  search?: string;
  /** A view that heads its own page — the shell draws no header over it, because the controls
   *  reaching the whole page are the view's own and stand beside its title. */
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

export const WORKSPACE_VIEWS: Record<WorkspaceTab, PaneView> = {
  team: {
    label: "Team",
    remountOnPlace: true,
    search: "Search members",
    render: (place, onPlace) => <Team place={place} onPlace={onPlace} />,
  },
  memory: {
    label: "Memory",
    remountOnPlace: false,
    search: "Search memory",
    render: (place, onPlace) => <Memory place={place} onPlace={onPlace} />,
  },
  sources: declared("Sources", SOURCES),
  connectors: {
    label: "Connectors",
    remountOnPlace: false,
    search: "Search connectors",
    render: (place) => <WorkspaceConnectors place={place} />,
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
  artifacts: {
    label: "Artifacts",
    remountOnPlace: false,
    search: "Search artifacts",
    render: (place, onPlace) => <Artifacts place={place} onPlace={onPlace} />,
  },
};

