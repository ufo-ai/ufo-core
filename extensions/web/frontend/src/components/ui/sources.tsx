import { useState } from "react";

import { IconFileText, IconWorld } from "@tabler/icons-react";

import {
  AVATAR_GROUP_MARK,
  AVATAR_GROUP_SHOWN,
  AvatarGroup,
  AvatarGroupCount,
} from "@/components/ui/avatar";
import { BrandMark } from "@/lib/brandMark";
import type { SourceRef } from "@/lib/types";

const FAVICON_SERVICE = "https://www.google.com/s2/favicons";
const FAVICON_SIZE = 32;

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
    <span data-slot="source-tile" title={source.title} className={AVATAR_GROUP_MARK}>
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

/** What the turn has read so far: the first few sites or records as a group of marks, everything
 *  past them as a count. */
export function Sources({ sources }: { sources: SourceRef[] }) {
  if (sources.length === 0) return null;
  const marks = tiled(sources);
  const shown = marks.slice(0, AVATAR_GROUP_SHOWN);
  const rest = marks.length - shown.length;
  return (
    <span data-slot="sources" className="flex min-w-0 items-center">
      <AvatarGroup label={marks.map((source) => source.title).join(", ")}>
        {shown.map((source) => (
          <SourceTile key={source.url || source.ref} source={source} />
        ))}
        {rest ? <AvatarGroupCount count={rest} /> : null}
      </AvatarGroup>
    </span>
  );
}
