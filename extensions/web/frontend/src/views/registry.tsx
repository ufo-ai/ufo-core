import type { ReactNode } from "react";

import { Listing, type ListingSpec } from "@/kernel/listing";
import type { Placement } from "@/kernel/pager";
import type { Section, WorkspaceTab } from "@/lib/route";
import { WorkspaceConnectors } from "@/views/Connectors";
import { ArchivedApps } from "@/views/ArchivedApps";
import { Memory } from "@/views/Memory";
import { SOURCES } from "@/views/Sources";
import { Team } from "@/views/Team";
import { WorkspaceSkills } from "@/views/WorkspaceSkills";
import { CREDENTIALS } from "@/views/WorkspaceCredentials";
import { WorkspaceBilling } from "@/views/Billing";
import { WorkspaceUsage } from "@/views/Usage";

export type PaneView = {
  label: string;
  /** Every view is handed the place it stands at and the way to change it — a row that took neither
   *  had to read the address itself and write its own, which is how one screen's route and the
   *  address came to disagree until the next navigation. */
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
  apps: {
    label: "Apps",
    remountOnPlace: false,
    render: () => <ArchivedApps />,
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
  sources: declared("Sources", SOURCES),
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

export const CONNECTORS: PaneView = {
  label: "Connectors",
  remountOnPlace: false,
  search: "Search connectors",
  render: (place, onPlace) => <WorkspaceConnectors place={place} onPlace={onPlace} />,
};

/** The sections the portal renders itself. A `Section` outside this record is a screen an app
 *  ships, and the router lands its address on that app. */
export const SECTION_VIEWS: Partial<Record<Section, PaneView>> = {
  connectors: CONNECTORS,
};
