import { useCallback, useState } from "react";
import type { ReactNode } from "react";
import { IconWorldWww } from "@tabler/icons-react";

import { buttonVariants } from "@/components/ui/button";
import { Segmented } from "@/components/ui/filter";
import { Sheet } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Clip, Lede, Table, Td, TdFact, TdWhole, Th, tableFloor } from "@/components/ui/table";
import { ArtifactText, FileSheet, MediaIcon, isTextMedia } from "@/kernel/artifact";
import { CardGrid } from "@/kernel/cards";
import { OWNER_FIELD, ObjectDetail, creator, objectAt, slotOf } from "@/kernel/objects";
import type { ObjectRow } from "@/kernel/objects";
import type { Placement } from "@/kernel/pager";
import { FacetMenu, PageToolbar, ToolbarRule, ViewSwitch } from "@/kernel/pane";
import type { Face, FacetGroup } from "@/kernel/pane";
import { Panel, PanelBlank, PanelEmpty, Section, usePanelRead } from "@/kernel/panel";
import type { PanelState } from "@/kernel/panel";
import { DataTable } from "@/kernel/table";
import { ownerLabel, slackLink, useViewer } from "@/lib/audience";
import { cn } from "@/lib/cn";
import { useMainAgent } from "@/lib/mainAgent";
import { Moment } from "@/lib/moments";
import { chatHash } from "@/lib/route";
import LOGO_SHEET_COVER from "@/assets/ufo-logo-ratio-cover.png?url";
import LOGO_SHEET_URL from "@/assets/ufo-logo-ratio.pdf?url";

const SITE_KIND = "site";

const NOT_FOUND = 404;
const SITE_FAMILY = "Sites";
/** Files a member attached to their own message rather than files an agent shared. */
const ATTACHMENT_FAMILY = "Attachments";
const MEDIA: Record<string, string> = {
  Images: "image",
  Documents: "document",
  Other: "other",
};

const FACETS: FacetGroup[] = [
  {
    label: "Kind",
    options: [
      { label: SITE_FAMILY, value: SITE_FAMILY },
      { label: ATTACHMENT_FAMILY, value: ATTACHMENT_FAMILY },
    ],
  },
  {
    label: "File type",
    options: Object.keys(MEDIA).map((family) => ({ label: family, value: family })),
  },
];
const FAMILIES = [SITE_FAMILY, ATTACHMENT_FAMILY, ...Object.keys(MEDIA)];

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
  name: string;
  filename: string;
  subject: string | null;
  media_type: string;
  size_bytes: number;
  shared_at: string;
  url: string | null;
  preview_url: string | null;
  owner_email: string | null;
  origin: string | null;
  conversation: string;
  surface: string;
  source: string | null;
};

type FilesPayload = {
  objects: Artifact[];
  next_cursor?: string | null;
};

type SitesPayload = { objects: ObjectRow[] };

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

/** `bare` is the workspace's own shelf holding nothing: the shipped sheet is on the page, so the
 *  cards are not empty, and the member still has to be told where their own files land. */
type Shelf = { cards: Card[]; files: FilesPayload; bare: boolean };

/** What the shelf states where the workspace has shared nothing yet. */
const NOTHING_SHARED = "A file or site an app makes in a conversation is listed here.";

const NO_FILES: PanelState<FilesPayload> = { phase: "ready", payload: { objects: [] } };
const NO_SITES: PanelState<SitesPayload> = { phase: "ready", payload: { objects: [] } };

function isImage(entry: Artifact): boolean {
  return entry.media_type.startsWith("image/");
}

function visibilityLabel(visibility: string) {
  return visibility.charAt(0).toUpperCase() + visibility.slice(1);
}

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

function siteCard(row: ObjectRow, viewer: string | null, owner: string): Card {
  const createdAt = typeof row.created_at === "string" ? row.created_at : null;
  return {
    key: slotOf({ agent: owner, kind: SITE_KIND, name: row.name }),
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

function tileExcerpt(file: Artifact | null): ReactNode {
  if (!file?.url || !isTextMedia(file.media_type)) return null;
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

/** The wrappers a compressed archive is named by. A `.tar.gz` is a tarball rather than a gzip file,
 *  so the pair names the format and the last suffix alone would say the wrong thing. */
const WRAPPER_SUFFIX = new Set(["gz", "bz2", "xz", "zst"]);

function extensionLabel(filename: string): string | null {
  const parts = filename.split(".");
  if (parts.length < 2) return null;
  const last = parts[parts.length - 1];
  const suffix =
    parts.length > 2 && WRAPPER_SUFFIX.has(last.toLowerCase())
      ? parts[parts.length - 2] + "." + last
      : last;
  return suffix.toUpperCase();
}

/** What a tile draws where the record has no picture of its own: a file's characters where the
 *  preview service can render them, and otherwise its extension. A site takes neither. */
function tileBody(card: Card): ReactNode {
  const excerpt = tileExcerpt(card.file);
  if (excerpt) return excerpt;
  if (!card.file) return null;
  const suffix = extensionLabel(card.file.filename);
  if (!suffix) return null;
  return (
    <span className="flex size-full items-center justify-center font-mono text-subtitle text-ink-soft">
      {suffix}
    </span>
  );
}

function fileKey(file: { name: string }): string {
  return file.name;
}

function fileCard(entry: Artifact, viewer: string | null): Card {
  return {
    key: fileKey(entry),
    name: entry.filename,
    time: moment(entry.shared_at),
    status: <Moment at={entry.shared_at} />,
    body: entry.subject,
    meta: ownerLabel(entry.owner_email, viewer),
    type: typeLabel(entry.media_type),
    image: entry.preview_url ?? (isImage(entry) ? entry.url : null),
    link: null,
    file: entry,
  };
}

const SHEET: Artifact = {
  name: "logo-sheet",
  filename: "ufo-logo-ratio.pdf",
  subject:
    "The wordmark, the mark's three dots on their golden-ratio construction, and the spacing " +
    "around both.",
  media_type: "application/pdf",
  size_bytes: 58240,
  shared_at: "",
  url: LOGO_SHEET_URL,
  preview_url: LOGO_SHEET_COVER,
  owner_email: null,
  origin: null,
  conversation: "",
  surface: "",
  source: null,
};

function shelf(
  sites: PanelState<SitesPayload>,
  files: PanelState<FilesPayload>,
  viewer: string | null,
  onSites: boolean,
  scope: Scope,
  owner: string | null,
  top: boolean,
  sheet: boolean,
): PanelState<Shelf> {
  if (files.phase !== "ready") return files;
  if (sites.phase === "loading") return sites;
  if (sites.phase === "failed" && sites.status !== NOT_FOUND) return sites;
  const shown =
    owner !== null && sites.phase === "ready" && (onSites || top)
      ? sites.payload.objects
          .filter((row) => scope === "all" || (viewer !== null && row[OWNER_FIELD] === viewer))
          .map((row) => siteCard(row, viewer, owner))
      : [];
  const shipped = sheet && !files.payload.next_cursor ? [fileCard(SHEET, viewer)] : [];
  const cards = [
    ...shown,
    ...files.payload.objects.map((entry) => fileCard(entry, viewer)),
    ...shipped,
  ];
  cards.sort((left, right) => (left.time < right.time ? 1 : left.time > right.time ? -1 : 0));
  const bare = !shown.length && !files.payload.objects.length;
  return { phase: "ready", payload: { cards, files: files.payload, bare } };
}

function nameOf(id: string, cards: Card[] | null): string | undefined {
  return objectAt(id)?.name ?? cards?.find((card) => card.key === id)?.name;
}

const SITE_VIEW_PAGE_WIDTH = 1280;
const SITE_VIEW_PAGE_HEIGHT = 800;

/** The same sandbox the apps screen gives a homepage: model-authored bytes, so the frame withholds
 *  `allow-top-navigation` and sends no referrer to the site's origin. */
const SITE_SANDBOX =
  "allow-scripts allow-same-origin allow-forms allow-popups allow-modals allow-downloads allow-pointer-lock";

function SiteView({ url, name }: { url: string; name: string }) {
  const [width, setWidth] = useState(SITE_VIEW_PAGE_WIDTH);
  const measure = useCallback((node: HTMLDivElement | null) => {
    if (node === null) return undefined;
    const read = () => setWidth(node.clientWidth || SITE_VIEW_PAGE_WIDTH);
    read();
    const watcher = new ResizeObserver(read);
    watcher.observe(node);
    return () => watcher.disconnect();
  }, []);
  return (
    <div
      ref={measure}
      className="relative aspect-[16/10] w-full shrink-0 overflow-hidden rounded-panel border border-edge"
    >
      <iframe
        src={url}
        title={name}
        sandbox={SITE_SANDBOX}
        referrerPolicy="no-referrer"
        className="absolute left-0 top-0 border-0"
        style={{
          width: SITE_VIEW_PAGE_WIDTH,
          height: SITE_VIEW_PAGE_HEIGHT,
          transform: "scale(" + width / SITE_VIEW_PAGE_WIDTH + ")",
          transformOrigin: "top left",
        }}
      />
    </div>
  );
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
  const after = place.after ?? "";
  const fileParams = new URLSearchParams({ order_by: "shared_at", order: "desc" });
  if (query) fileParams.set("q", query);
  if (media) fileParams.set("media", media);
  if (picked === ATTACHMENT_FAMILY) fileParams.set("attachment", "true");
  if (scope !== "all") fileParams.set("mine", "true");
  if (after) fileParams.set("cursor", after);
  const walked = usePanelRead<FilesPayload>(
    picked === SITE_FAMILY ? null : "/objects/artifact?" + fileParams.toString(),
  );
  const onSites = picked === SITE_FAMILY;
  // Attachments narrows to the files a member attached, so it hides the sites the way a file-type
  // value does; only the default view and the Sites kind draw the site cards.
  const siteOwner =
    mainAgent && !media && picked !== ATTACHMENT_FAMILY ? mainAgent.id : null;
  const state = shelf(
    siteOwner ? held : NO_SITES,
    onSites ? NO_FILES : walked,
    viewer,
    onSites,
    scope,
    siteOwner,
    !after,
    scope === "all" && !picked && !query,
  );

  const selected = place.opens?.at(-1);
  const opens = selected ? [selected] : [];
  const cards = state.phase === "ready" ? state.payload.cards : null;
  const sites = held.phase === "ready" ? held.payload.objects : null;

  const unknown = Boolean(picked) && !FAMILIES.includes(picked);
  const absent = held.phase === "failed" && held.status === NOT_FOUND;
  const facets = absent ? FACETS.filter((group) => group.label !== "Kind") : FACETS;

  return (
    <>
      <PageToolbar>
        <Segmented
          label="Scope"
          segments={SCOPE_SEGMENTS}
          value={scope}
          onPick={(value) =>
            onPlace({ scope: value === "all" ? undefined : value, after: undefined })
          }
        />
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
      <Section>
        <Panel state={state} loading={() => <ShelfSkeleton face={face} />}>
          {(payload) => {
            if (unknown) return <PanelBlank body="That filter is not available." />;
            if (!payload.cards.length)
              return <PanelBlank body={query || picked ? "Nothing matches." : NOTHING_SHARED} />;
            const open = (card: Card) => () => onPlace({ opens: [card.key] });
            return (
              <>
                {payload.bare ? (
                  <div className="mb-lg">
                    <PanelBlank body={NOTHING_SHARED} />
                  </div>
                ) : null}
                {face === "table" ? (
                  <Shapes cards={payload.cards} opens={opens} open={open} />
                ) : (
                  <CardGrid
                    rows={payload.cards}
                    rowKey={(card) => card.key}
                    mark={{
                      shape: "tile",
                      image: (card) => card.image,
                      body: (card) => tileBody(card),
                    }}
                    primary={(card) => card.name}
                    status={(card) => card.status}
                    open={(card) => (card.file && !card.file.url ? null : open(card))}
                    current={(card) => opens.includes(card.key)}
                    action={(card) => (card.link ? <OpenSite href={card.link} /> : null)}
                  />
                )}
                {payload.files.next_cursor || after ? (
                  <div className="flex gap-sm">
                    {payload.files.next_cursor ? (
                      <button
                        className={cn(buttonVariants({ variant: "row" }))}
                        onClick={() =>
                          onPlace({ after: payload.files.next_cursor ?? undefined })
                        }
                      >
                        Next page
                      </button>
                    ) : null}
                    {after ? (
                      <button
                        className={cn(buttonVariants({ variant: "row" }))}
                        onClick={() => onPlace({ after: undefined })}
                      >
                        First page
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </>
            );
          }}
        </Panel>
        {opens.slice(-1).map((id) => (
          <Opened
            key={id}
            id={id}
            cards={cards}
            sites={sites}
            onOpen={(next) => onPlace({ opens: [next] })}
            onClose={() => onPlace({ opens: undefined })}
          />
        ))}
      </Section>
    </>
  );
}

/** The selected file or site in the shelf's shared sheet. */
function Opened({
  id,
  cards,
  sites,
  onOpen,
  onClose,
}: {
  id: string;
  cards: Card[] | null;
  sites: ObjectRow[] | null;
  onOpen: (id: string) => void;
  onClose: () => void;
}) {
  const at = objectAt(id);
  const card = cards?.find((entry) => entry.key === id) ?? null;
  const stranded = at === null && cards !== null && !card?.file;
  const site =
    at !== null && at.kind === SITE_KIND ? sites?.find((row) => row.name === at.name) : undefined;
  const url = typeof site?.site_url === "string" ? site.site_url : null;
  if (card?.file) {
    return (
      <FileSheet
        file={card.file}
        onClose={onClose}
        details={card.file.conversation ? <ArtifactDetails entry={card.file} /> : undefined}
      />
    );
  }
  if (at !== null) {
    return (
      <ObjectDetail
        agentId={at.agent}
        kind={at.kind}
        name={at.name}
        lead={url ? <SiteView url={url} name={at.name} /> : undefined}
        actions={() => (url ? <OpenSite href={url} /> : undefined)}
        onOpen={(next) => onOpen(slotOf(next))}
        onBack={onClose}
      />
    );
  }
  return (
    <Sheet open title={stranded ? id : (nameOf(id, cards) ?? id)} onClose={onClose}>
      {stranded ? <PanelEmpty>That item is not on this page.</PanelEmpty> : null}
    </Sheet>
  );
}

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

const COLUMNS = [{ label: "Name", whole: true }, "Details", { label: "Type", fact: true }];

const COLUMN_TRACK: Record<string, string> = {
  Details: "w-(--size-prose-column)",
  Type: "w-(--size-fact-column)",
};

const SKELETON_TILES = 8;
const SKELETON_ROWS = 6;

/** The columns and the view are the member's rather than the read's, so the head names them while
 *  the records are still coming and nothing shifts when they land. */
function ShelfSkeleton({ face }: { face: Face }) {
  if (face === "table")
    return (
      <Table measured floor={tableFloor({ prose: 2, fact: 1, act: true })}>
        <thead>
          <tr>
            {COLUMNS.map((column) => {
              const name = typeof column === "string" ? column : column.label;
              return (
                <Th key={name} className={COLUMN_TRACK[name]}>
                  {name}
                </Th>
              );
            })}
            <Th className="w-(--size-act)">{""}</Th>
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: SKELETON_ROWS }, (_, index) => (
            <tr key={index}>
              <TdWhole>
                <span className="flex min-w-0 items-center gap-md">
                  <Skeleton className="size-(--size-lede) shrink-0" />
                  <Skeleton className="h-(--size-notice) w-(--size-prose-column) text-label" />
                </span>
              </TdWhole>
              <Td>
                <Skeleton className="h-(--size-notice) w-(--size-prose-column) text-label" />
              </Td>
              <TdFact>
                <Skeleton className="h-(--size-notice) w-2/3 text-label" />
              </TdFact>
              <Td className="w-(--size-act)">
                <Skeleton className="ml-auto h-(--size-notice) w-2/3 text-label" />
              </Td>
            </tr>
          ))}
        </tbody>
      </Table>
    );
  return (
    <ul className="m-0 grid list-none grid-cols-[repeat(auto-fill,minmax(var(--size-tile),1fr))] gap-lg p-0">
      {Array.from({ length: SKELETON_TILES }, (_, index) => (
        <li key={index} className="flex flex-col gap-sm">
          <Skeleton className="aspect-square w-full rounded-panel border border-edge" />
          <Skeleton className="h-(--size-notice) w-4/5 text-body" />
          <Skeleton className="h-(--size-notice) w-2/5 text-small" />
        </li>
      ))}
    </ul>
  );
}

/** The list view of the shelf. The cards are the other view the bar offers, so a narrow pane does
 *  not stack these rows into cards of its own: the table holds its tracks and scrolls sideways. */
function Shapes({
  cards,
  opens,
  open,
}: {
  cards: Card[];
  opens: string[];
  open: (card: Card) => () => void;
}) {
  return (
    <DataTable
      columns={COLUMNS}
      stacks={false}
      rows={cards}
      rowKey={(card) => card.key}
      empty={NOTHING_SHARED}
      open={(card) => (card.file && !card.file.url ? null : open(card))}
      current={(card) => opens.includes(card.key)}
      act={(card) => (card.file && !card.file.url ? null : "Open")}
    >
      {(card) => (
        <>
          <TdWhole>
            <Lede mark={<Mark card={card} />} whole>
              {card.name}
            </Lede>
          </TdWhole>
          <Td>
            <Clip>{card.body ?? card.meta}</Clip>
          </Td>
          <TdFact>{card.type}</TdFact>
        </>
      )}
    </DataTable>
  );
}

function Mark({ card }: { card: Card }) {
  if (card.image) return <TileImage src={card.image} />;
  if (!card.file) return <IconWorldWww className="size-icon" aria-hidden />;
  return <MediaIcon mediaType={card.file.media_type} />;
}

function TileImage({ src }: { src: string }) {
  const [loaded, setLoaded] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const reveal = useCallback(
    (held: HTMLImageElement | null) => {
      if (held !== null && held.complete && held.naturalWidth > 0) setLoaded(src);
    },
    [src],
  );
  if (failed === src)
    return <IconWorldWww className="size-icon" aria-hidden />;
  return (
    <img
      ref={reveal}
      alt=""
      src={src}
      loading="lazy"
      decoding="async"
      onLoad={() => setLoaded(src)}
      onError={() => setFailed(src)}
      className={cn(
        "size-full object-cover transition-opacity duration-200 ease-control motion-reduce:transition-none",
        loaded === src ? "opacity-100" : "opacity-0",
      )}
    />
  );
}

function ArtifactDetails({ entry }: { entry: Artifact }) {
  const viewer = useViewer();
  const thread = slackLink(entry.surface, entry.source);
  const out = "text-inherit no-underline hover:underline focus-visible:underline";
  const meta = [
    entry.owner_email ? ownerLabel(entry.owner_email, viewer) : null,
    entry.origin,
  ]
    .filter((part) => part)
    .join(" · ");

  return (
    <>
      <div className="font-mono text-small text-ink-soft">
        {meta ? meta + " · " : null}
        <Moment at={entry.shared_at} />
      </div>
      <div className="flex flex-wrap items-center gap-sm">
        <span className="flex flex-wrap gap-x-sm font-mono text-small text-ink-soft">
          <a href={chatHash(entry.conversation)} className={out}>
            Conversation
          </a>
          {thread ? (
            <a href={thread} target="_blank" rel="noopener noreferrer" className={out}>
              Slack <span aria-hidden>↗</span>
            </a>
          ) : null}
        </span>
      </div>
    </>
  );
}
