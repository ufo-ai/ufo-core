import { IconWorldWww } from "@tabler/icons-react";
import { useState, type ReactNode } from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import { Segmented } from "@/components/ui/filter";
import { Sheet } from "@/components/ui/sheet";
import { Lede, Td, TdFact } from "@/components/ui/table";
import { ArtifactText, MediaIcon, isTextMedia } from "@/kernel/artifact";
import { useBeside } from "@/kernel/beside";
import { CardGrid } from "@/kernel/cards";
import {
  OWNER_FIELD,
  ObjectDetail,
  creator,
  type ObjectAddress,
  type ObjectRow,
} from "@/kernel/objects";
import { Pager, type Placement } from "@/kernel/pager";
import {
  FacetMenu,
  PageToolbar,
  ToolbarRule,
  ViewSwitch,
  type Face,
  type FacetGroup,
} from "@/kernel/pane";
import {
  Panel,
  PanelBlank,
  PanelEmpty,
  Section,
  usePanelRead,
  type PanelState,
} from "@/kernel/panel";
import { DataTable } from "@/kernel/table";
import { ownerLabel, slackLink, useViewer } from "@/lib/audience";
import { cn } from "@/lib/cn";
import { useMainAgent } from "@/lib/mainAgent";
import { Moment } from "@/lib/moments";
import { chatHash } from "@/lib/route";
import { formatSize } from "@/lib/size";

const SITE_KIND = "site";

const OBJECT_PREFIX = "object/";
const NOT_FOUND = 404;
export const SITE_FAMILY = "Sites";
const MEDIA: Record<string, string> = {
  Images: "image",
  Documents: "document",
  Other: "other",
};

/** Every narrowing the shelf offers, by the axis a member thinks in. A site and a shared file are
 *  read from different places and share no facts, so which of the two a member wants is its own
 *  question — asked once, above the file types, rather than as a fourth entry in a list of media
 *  kinds it does not belong in. */
const FACETS: FacetGroup[] = [
  { label: "Kind", options: [{ label: SITE_FAMILY, value: SITE_FAMILY }] },
  {
    label: "File type",
    options: Object.keys(MEDIA).map((family) => ({ label: family, value: family })),
  },
];
const FAMILIES = [SITE_FAMILY, ...Object.keys(MEDIA)];

/** What the member picked, or the default the screen opens on. Every one of these reads its value
 *  off the place and nowhere else, so a screen opened fresh stands on its defaults — the leading
 *  choice of each control, at rest — and a screen opened from a link stands exactly where the link
 *  says. A choice held anywhere but the address would open one member's first screen on another
 *  member's last one, and no link could carry it. */
function asFace(value: string | undefined): Face {
  return value === "table" ? value : "tiles";
}

type Scope = "created" | "all";

const SCOPE_SEGMENTS = [
  { label: "All", value: "all" },
  { label: "Created by me", value: "created" },
];

function asScope(value: string | undefined): Scope {
  return value === "created" ? value : "all";
}

type Artifact = {
  filename: string;
  subject: string | null;
  media_type: string;
  size_bytes: number;
  created_at: string;
  url: string | null;
  preview_url: string | null;
  owner_email: string | null;
  origin: string | null;
  conversation_id: string;
  surface: string;
  source: string | null;
};

type FilesPayload = {
  artifacts: Artifact[];
  newer?: string | null;
  older?: string | null;
};

type SitesPayload = { objects: ObjectRow[] };

/** One card of the shelf. A site and a shared file are two families of the one thing the member
 *  came for — what the agents produced — so they are read down one grid, newest first whichever
 *  family a card belongs to. `time` is what that order is taken on: a record the shelf cannot date
 *  sorts last rather than sorting arbitrarily. `type` is the one short fact the two families
 *  share, and it is what the table's own column compares straight down. */
type Card = {
  key: string;
  name: string;
  time: number;
  status: ReactNode;
  body: string | null;
  meta: string;
  type: string;
  image: string | null;
  link: string | null;
  file: Artifact | null;
};

type Shelf = { cards: Card[]; files: FilesPayload };

const NO_FILES: PanelState<FilesPayload> = { phase: "ready", payload: { artifacts: [] } };
const NO_SITES: PanelState<SitesPayload> = { phase: "ready", payload: { objects: [] } };

function isImage(entry: Artifact): boolean {
  return entry.media_type.startsWith("image/");
}

function visibilityLabel(visibility: string) {
  return visibility.charAt(0).toUpperCase() + visibility.slice(1);
}

/** The one short word a file's type reduces to, which is what the member recognises it by: the
 *  subtype of the media type, with the vendor prefixes an office format carries dropped. The full
 *  media type stays in the viewer, where there is room to state it exactly. */
function typeLabel(mediaType: string): string {
  const subtype = mediaType.split("/")[1] ?? mediaType;
  return subtype.split(/[.+]/).pop() ?? subtype;
}

/** One stamp as the order reads it. A record with no date, or a date the shelf cannot read, takes
 *  the floor and stands at the foot of the shelf. */
function moment(iso: string | null): number {
  const at = iso === null ? NaN : Date.parse(iso);
  return Number.isNaN(at) ? Number.NEGATIVE_INFINITY : at;
}

function siteCard(row: ObjectRow, viewer: string | null): Card {
  const createdAt = typeof row.created_at === "string" ? row.created_at : null;
  return {
    key: OBJECT_PREFIX + SITE_KIND + "/" + row.name,
    name: row.name,
    time: moment(createdAt),
    status: <Moment at={createdAt} />,
    body: typeof row.summary === "string" ? row.summary : null,
    meta: [
      "Site",
      typeof row.visibility === "string" ? visibilityLabel(row.visibility) : null,
      creator(row[OWNER_FIELD], viewer),
    ]
      .filter((part) => part)
      .join(" · "),
    type: "Site",
    image: typeof row.preview_url === "string" ? row.preview_url : null,
    link: typeof row.site_url === "string" ? row.site_url : null,
    file: null,
  };
}

const FINGERPRINT_MEDIA = new Set([
  "application/json",
  "text/csv",
  "text/markdown",
  "text/x-patch",
]);

/** A shaped text file's tile is its fingerprint: the content renders at reading width — markdown
 *  as the document it is, a csv as its table, json and a patch as their characters — and scales to
 *  the edge of legibility, so the tile holds a dense picture of the page rather than one
 *  full-sized opening line. It reads the same link the viewer reads whole, the one preview an
 *  already-shared file can grow without a re-render; plain text stays pictureless, since it has no
 *  shape a fingerprint would carry. The fingerprint is `inert`: the tile is decoration, so nothing
 *  in it takes a press or the keyboard, and the tile's own overflow crops it. */
function tileExcerpt(file: Artifact | null): ReactNode {
  if (!file?.url || !FINGERPRINT_MEDIA.has(file.media_type)) return null;
  return (
    <div inert className="w-(--size-fingerprint) origin-top-left scale-(--scale-fingerprint)">
      <ArtifactText
        url={file.url}
        name={file.filename}
        mediaType={file.media_type}
        display="inline"
      />
    </div>
  );
}

function fileCard(entry: Artifact, viewer: string | null): Card {
  return {
    key: entry.created_at + "|" + entry.filename,
    name: entry.filename,
    time: moment(entry.created_at),
    status: <Moment at={entry.created_at} />,
    body: entry.subject,
    meta: ownerLabel(entry.owner_email, viewer),
    type: typeLabel(entry.media_type),
    image: entry.preview_url ?? (isImage(entry) ? entry.url : null),
    link: null,
    file: entry,
  };
}

/** The files walk is the shelf's own read: it fails the section, and it is the one page a cursor
 *  continues. A deploy with no sites extension answers the site read with a 404, which is a family
 *  that does not exist here rather than a fault — every other refusal is stated.
 *
 *  The sites arrive whole on every read, and a shelf mixing them into a walk of files has to say
 *  which page each one stands on. They stand on the newest: a site is a place that goes on being
 *  worked on rather than a file dated once, so the top of the shelf is where a member looks for
 *  it, and the family's own narrowing lists every one of them at any depth. Which page is the
 *  newest is read off the files payload rather than off the cursor in the address — a member who
 *  walked back up to the top is on the newest page and carries a cursor saying so, and a shelf
 *  judging by the cursor alone would drop the sites the moment they walked back to them. */
function shelf(
  sites: PanelState<SitesPayload>,
  files: PanelState<FilesPayload>,
  viewer: string | null,
  onSites: boolean,
  scope: Scope,
): PanelState<Shelf> {
  if (files.phase !== "ready") return files;
  if (sites.phase === "loading") return sites;
  if (sites.phase === "failed" && sites.status !== NOT_FOUND) return sites;
  const shown =
    sites.phase === "ready" && (onSites || !files.payload.newer)
      ? sites.payload.objects.filter((row) => {
          if (scope === "all") return true;
          return viewer !== null && row[OWNER_FIELD] === viewer;
        })
      : [];
  const cards = [
    ...shown.map((row) => siteCard(row, viewer)),
    ...files.payload.artifacts.map((entry) => fileCard(entry, viewer)),
  ];
  cards.sort((left, right) => (left.time < right.time ? 1 : left.time > right.time ? -1 : 0));
  return { phase: "ready", payload: { cards, files: files.payload } };
}

function objectAt(open: string | undefined): ObjectAddress | null {
  if (!open?.startsWith(OBJECT_PREFIX)) return null;
  const rest = open.slice(OBJECT_PREFIX.length);
  const cut = rest.indexOf("/");
  return cut < 0 ? null : { kind: rest.slice(0, cut), name: rest.slice(cut + 1) };
}

export function Artifacts({
  place,
  onPlace,
}: {
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const mainAgent = useMainAgent();
  const viewer = useViewer();
  const scope = asScope(place.scope);
  const face = asFace(place.face);
  const query = place.q ?? "";
  const picked = place.chip ?? "";
  const media = MEDIA[picked];
  const siteParams = new URLSearchParams({
    agent: mainAgent?.id ?? "",
    order_by: "created_at",
    order: "desc",
  });
  if (query) siteParams.set("q", query);
  const held = usePanelRead<SitesPayload>(
    mainAgent ? "/objects/" + SITE_KIND + "?" + siteParams.toString() : null,
  );
  const fileParams = new URLSearchParams();
  if (query) fileParams.set("q", query);
  if (media) fileParams.set("media", media);
  if (scope !== "all") fileParams.set("scope", scope);
  if (place.after) fileParams.set("after", place.after);
  const walked = usePanelRead<FilesPayload>(
    picked === SITE_FAMILY
      ? null
      : "/workspace/artifacts" + (fileParams.size ? "?" + fileParams.toString() : ""),
  );
  const onSites = picked === SITE_FAMILY;
  const state = shelf(
    mainAgent && !media ? held : NO_SITES,
    onSites ? NO_FILES : walked,
    viewer,
    onSites,
    scope,
  );

  const at = objectAt(place.open);
  const detail = useBeside(
    at !== null && at.name !== null && mainAgent ? (
      <ObjectDetail
        key={at.kind + "/" + at.name}
        agentId={mainAgent.id}
        kind={at.kind}
        name={at.name}
        onOpen={(next) => onPlace({ open: OBJECT_PREFIX + next.kind + "/" + (next.name ?? "") })}
        onBack={() => onPlace({ open: undefined })}
      />
    ) : null,
    () => onPlace({ open: undefined }),
  );

  const unknown = Boolean(picked) && !FAMILIES.includes(picked);
  const absent = held.phase === "failed" && held.status === NOT_FOUND;
  const facets = absent ? FACETS.filter((group) => group.label !== "Kind") : FACETS;

  return (
    <>
      {state.phase === "loading" ? null : (
        <PageToolbar>
          <Segmented
            label="Scope"
            segments={SCOPE_SEGMENTS}
            value={scope}
            onPick={(value) =>
              onPlace({ scope: value === "all" ? undefined : value, after: undefined })
            }
          />
          {state.phase === "failed" && place.after ? (
            <Button variant="row" onClick={() => onPlace({ after: undefined, open: undefined })}>
              First page
            </Button>
          ) : null}
          <span className="ml-auto flex shrink-0 items-center gap-sm max-narrow:ml-0">
            <FacetMenu
              groups={facets}
              value={picked}
              onPick={(value) => onPlace({ chip: value || undefined, after: undefined })}
            />
            <ToolbarRule />
            <ViewSwitch
              face={face}
              onPick={(next) => onPlace({ face: next === "tiles" ? undefined : next })}
            />
          </span>
        </PageToolbar>
      )}
      <Section>
        <Panel state={state} shape={face === "table" ? "table" : "cards"}>
          {(payload) => {
            if (unknown) return <PanelBlank body="That filter is not available." />;
            if (!payload.cards.length)
              return (
                <PanelBlank
                  body={
                    query || picked
                      ? "Nothing matches."
                      : "A file or site an app makes in a conversation is listed here."
                  }
                />
              );
            const opened = payload.cards.find((card) => card.key === place.open);
            const open = (card: Card) => () => onPlace({ open: card.key });
            return (
              <>
                {face === "table" ? (
                  <Shapes cards={payload.cards} open={open} />
                ) : (
                  <CardGrid
                    rows={payload.cards}
                    rowKey={(card) => card.key}
                    mark={{
                      shape: "tile",
                      image: (card) => card.image,
                      body: (card) => tileExcerpt(card.file),
                    }}
                    primary={(card) => card.name}
                    status={(card) => card.status}
                    open={(card) => (card.file && !card.file.url ? null : open(card))}
                    action={(card) => (card.link ? <OpenSite href={card.link} /> : null)}
                  />
                )}
                {onSites ? null : <Pager payload={payload.files} onPlace={onPlace} />}
                {place.open && !at ? (
                  opened?.file ? (
                    <Viewer entry={opened.file} onClose={() => onPlace({ open: undefined })} />
                  ) : (
                    <PanelEmpty>That item is not on this page.</PanelEmpty>
                  )
                ) : null}
              </>
            );
          }}
        </Panel>
      </Section>
      {detail}
    </>
  );
}

/** A site's own address, which is the one thing about it the portal cannot draw for the member.
 *  It leads out of the portal, so it says so and opens where a link out always does. It is laid
 *  over the tile's own picture, so it carries the page's surface under it rather than whatever the
 *  picture happens to be showing there. */
function OpenSite({ href }: { href: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(buttonVariants({ variant: "row" }), "bg-surface no-underline")}
    >
      Open <span aria-hidden>↗</span>
    </a>
  );
}

const COLUMNS = ["Name", "Details", { label: "Type", fact: true }];

/** The shelf as facts in columns. A record leads with the same picture its tile is drawn from,
 *  at the row's own pitch — a name alone makes the member read every line to find the file they
 *  would have recognised at a glance, and a file with no picture states its type as a glyph so the
 *  column of marks stays a column rather than a run of gaps. */
function Shapes({ cards, open }: { cards: Card[]; open: (card: Card) => () => void }) {
  return (
    <DataTable
      columns={COLUMNS}
      rows={cards}
      rowKey={(card) => card.key}
      empty="A file or site an app makes in a conversation is listed here."
      open={(card) => (card.file && !card.file.url ? null : open(card))}
      act={(card) => (card.file && !card.file.url ? null : "Open")}
    >
      {(card) => (
        <>
          <Td>
            <Lede mark={<Mark card={card} />}>{card.name}</Lede>
          </Td>
          <Td>{card.body ?? card.meta}</Td>
          <TdFact>{card.type}</TdFact>
        </>
      )}
    </DataTable>
  );
}

function Mark({ card }: { card: Card }) {
  if (card.image) return <img alt="" src={card.image} className="size-full object-cover" />;
  if (!card.file) return <IconWorldWww className="size-icon" aria-hidden />;
  return <MediaIcon mediaType={card.file.media_type} />;
}

function Viewer({ entry, onClose }: { entry: Artifact; onClose: () => void }) {
  const viewer = useViewer();
  const thread = slackLink(entry.surface, entry.source);
  const out = "text-inherit no-underline hover:underline focus-visible:underline";
  const meta = [
    entry.subject,
    entry.owner_email ? ownerLabel(entry.owner_email, viewer) : null,
    entry.origin,
    entry.media_type,
    formatSize(entry.size_bytes),
  ]
    .filter((part) => part)
    .join(" · ");

  return (
    <Sheet
      open
      onClose={onClose}
      title={entry.filename}
      actions={
        entry.url ? (
          <a
            href={entry.url}
            download={entry.filename}
            className={cn(buttonVariants({ variant: "send" }), "shrink-0 no-underline")}
          >
            Download
          </a>
        ) : null
      }
    >
      <div className="font-mono text-small text-ink-soft">
        {meta} · <Moment at={entry.created_at} />
      </div>
      <div className="flex flex-wrap gap-x-lg font-mono text-small text-ink-soft">
        <a href={chatHash(entry.conversation_id)} className={out}>
          Conversation
        </a>
        {thread ? (
          <a href={thread} target="_blank" rel="noopener noreferrer" className={out}>
            Slack <span aria-hidden>↗</span>
          </a>
        ) : null}
      </div>
      {isImage(entry) || entry.preview_url ? (
        <FullImage entry={entry} />
      ) : isTextMedia(entry.media_type) ? (
        <ArtifactText
          url={entry.url}
          name={entry.filename}
          mediaType={entry.media_type}
          display="inline"
        />
      ) : (
        <div className="font-mono text-small text-ink-soft">
          No preview for this file type. Download it to open it.
        </div>
      )}
    </Sheet>
  );
}

function FullImage({ entry }: { entry: Artifact }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <div className="font-mono text-small text-ink-soft">
        The image did not load. Its link may have expired — reload the listing.
      </div>
    );
  }
  return (
    <img
      alt={entry.subject || entry.filename}
      src={entry.preview_url ?? entry.url ?? ""}
      onError={() => setFailed(true)}
      className="max-h-(--media-tall) max-w-full object-contain"
    />
  );
}
