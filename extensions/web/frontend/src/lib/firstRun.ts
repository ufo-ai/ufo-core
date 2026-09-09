import type { ActionView } from "@/lib/types";

export const FIRST_RUN_READ = "/workspace/first-run";

/** The install happens on the provider's own pages, so nothing here can say when it lands — the wait is
 *  measured in the seconds the member spends over there. */
export const WATCH_MS = 3_000;

export type ProviderTile = { name: string; label: string; summary: string; group: string };

type Connector = ProviderTile & { installed: boolean };

export type FirstRunPayload = {
  providers: ProviderTile[];
  connectors: Connector[];
  actions: { member: ActionView[]; memory: ActionView[]; enrichment_profile: ActionView[] };
  model_key_held: boolean;
  workspace_domain: string | null;
};
