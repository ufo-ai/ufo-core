import { useState } from "react";

import { IconFileText, IconWorld } from "@tabler/icons-react";

import { Marker, MarkerContent } from "@/components/ui/marker";
import { BrandMark } from "@/lib/brandMark";
import type { SourceRef } from "@/lib/types";

const FAVICON_SERVICE = "https://www.google.com/s2/favicons";
const FAVICON_SIZE = 32;
const SOURCE_TILES_PER_ROW = 8;

/** The favicon service answers by host, so an address that names none draws the globe instead. */
export function faviconUrl(url: string): string | null {
  if (!URL.canParse(url)) return null;
  const host = new URL(url).hostname;
  return host ? `${FAVICON_SERVICE}?domain=${encodeURIComponent(host)}&sz=${FAVICON_SIZE}` : null;
}

/** One place the turn read, as a tile: the site's favicon, or a workspace page's provider mark.
 *  The title is the tooltip and the accessible name; a web tile opens its address. */
export function SourceTile({ source }: { source: SourceRef }) {
  const [broken, setBroken] = useState(false);
  const favicon = source.kind === "web" && !broken ? faviconUrl(source.url ?? "") : null;
  const Glyph = source.kind === "web" ? IconWorld : IconFileText;
  const drawn = favicon ? (
    <img
      src={favicon}
      alt=""
      onError={() => setBroken(true)}
      className="block size-(--size-site-icon) rounded-site-icon"
    />
  ) : source.kind === "workspace" && source.provider ? (
    <BrandMark provider={source.provider} className="size-(--size-site-icon)" />
  ) : (
    <Glyph className="size-(--size-site-icon) text-ink-soft" aria-hidden />
  );
  const tile = (
    <span
      data-slot="source-tile"
      title={source.title}
      className="grid size-(--size-site-tile) shrink-0 place-items-center rounded-key bg-tile"
    >
      {drawn}
      <span className="sr-only">{source.title}</span>
    </span>
  );
  if (!source.url) return tile;
  return (
    <a href={source.url} target="_blank" rel="noreferrer" className="no-underline">
      {tile}
    </a>
  );
}

/** A web tile stands for its site, so two pages of one host share the first page's tile; every
 *  other kind is one tile per record. */
function tiled(items: SourceRef[]): SourceRef[] {
  const seen = new Set<string>();
  return items.filter((source) => {
    const url = source.url ?? "";
    const host = URL.canParse(url) ? new URL(url).hostname : url;
    const key = source.kind === "web" ? host : (source.ref ?? "");
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/** What the turn has read so far, one row: a tile per site or record, then how many. */
export function Sources({ sources }: { sources: SourceRef[] }) {
  if (sources.length === 0) return null;
  return (
    <Marker data-slot="sources" className="mt-2xs">
      <span className="flex items-center gap-hair">
        {tiled(sources)
          .slice(0, SOURCE_TILES_PER_ROW)
          .map((source) => <SourceTile key={source.url || source.ref} source={source} />)}
      </span>
      <MarkerContent>{sources.length + (sources.length === 1 ? " source" : " sources")}</MarkerContent>
    </Marker>
  );
}
