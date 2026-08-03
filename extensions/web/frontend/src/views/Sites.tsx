import type { ListingSpec } from "@/kernel/listing";

type Site = { name: string; summary: string };

type SitesPayload = { available: boolean; sites: Site[] };

export const SITES: ListingSpec<SitesPayload, Site> = {
  read: "/workspace/sites",
  rows: (payload) => payload.sites,
  rowKey: (site) => site.name,
  list: { primary: { field: "name" }, meta: [{ field: "summary" }] },
  empty: "No sites are hosted.",
  unavailable: (payload) => (payload.available ? null : "No sites extension is installed."),
};
