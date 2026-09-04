// The artifacts app's page: a static site built with the portal's app kit. Edit this file and redeploy
// to change the page.

import {
  ArtifactText,
  CardGrid,
  Clip,
  DataTable,
  FacetMenu,
  FileSheet,
  IconWorldWww,
  Lede,
  MediaIcon,
  Moment,
  OWNER_FIELD,
  ObjectDetail,
  PageToolbar,
  Panel,
  PanelBlank,
  PanelEmpty,
  Section,
  SectionApp,
  Segmented,
  Sheet,
  Td,
  TdFact,
  TdWhole,
  ToolbarRule,
  ViewSwitch,
  buttonVariants,
  chatHash,
  cn,
  creator,
  isTextMedia,
  mountApp,
  objectAt,
  ownerLabel,
  slackLink,
  slotOf,
  useCallback,
  useMainAgent,
  usePanelRead,
  useState,
  useViewer,
} from "ufo/kit";
import type { Face, FacetGroup, ObjectRow, PanelState, Placement, ReactNode } from "ufo/kit";
// The logo sheet the deploy ships, committed beside this file. `?url` is vite's own, so the bytes
// ride with the page in this build and in the build a member's redeploy runs.
import LOGO_SHEET_URL from "./ufo-logo-ratio.pdf?url";
import LOGO_SHEET_COVER from "./ufo-logo-ratio-cover.png?url";

const SITE_KIND = "site";

const NOT_FOUND = 404;
const SITE_FAMILY = "Sites";
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

/** A text file's tile is its fingerprint: the content renders at reading width — markdown as the
 *  document it is, a csv as its table, code and plain text as their characters — and scales to the
 *  edge of legibility, so the tile holds a dense picture of the page rather than one full-sized
 *  opening line. Every file the sheet reads as text draws one, so the same file previews on the
 *  shelf and in its record; a type the preview service cannot render draws its extension. It
 *  reads the same link the viewer reads whole, the one preview an already-shared file can grow
 *  without a re-render. The fingerprint is `inert`: the tile is decoration, so nothing in it takes
 *  a press or the keyboard, and the tile's own overflow crops it. */
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

/** What a file states about itself when it has neither a picture nor characters the page can read:
 *  its extension, uppercased. A member scanning the shelf reads ZIP, PDF or TAR.GZ off the tile
 *  rather than a row of empty boxes that differ in nothing. A name with no suffix states nothing. */
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

/** The logo sheet the deploy ships, listed as the file it is. No workspace holds a row for it, so
 *  it carries no date, no owner, and no conversation: an undated file takes the floor of the order,
 *  which stands it under every file a member came for. */
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

/** The files listing is the shelf's own read: it fails the section. A deploy with no sites
 *  extension answers the site read with a 404, which is a family that does not exist here rather
 *  than a fault — every other refusal is stated.
 *
 *  The sites arrive whole on every read, and a shelf mixing them into a listing of files has to
 *  say which page each one stands on. They stand on the newest: a site is a place that goes on
 *  being worked on rather than a file dated once, so the top of the shelf is where a member looks
 *  for it, and the family's own narrowing lists every one of them at any depth. A page a cursor
 *  continues is not the top.
 *
 *  The shipped sheet closes the shelf, and closes it once: it stands on the page with no older
 *  files behind it. It is the page's own card rather than a file the workspace holds, so it never
 *  answers for the member's shelf: `bare` stays true under it. */
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

/** The site itself at the head of its record: the page the shelf's card only pictures, live and
 *  taking the pointer, laid out at a desktop page's width and scaled to the record column — the
 *  column is fluid, so the scale follows its measure. The frame is the site surface's own trusted
 *  page and carries the sandbox around the model-authored bytes itself, so this iframe takes no
 *  sandbox attribute, for the reason the apps screen's frame states. */
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

function Artifacts({
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
  if (scope !== "all") fileParams.set("mine", "true");
  if (after) fileParams.set("cursor", after);
  const walked = usePanelRead<FilesPayload>(
    picked === SITE_FAMILY ? null : "/objects/artifact?" + fileParams.toString(),
  );
  const onSites = picked === SITE_FAMILY;
  const siteOwner = mainAgent && !media ? mainAgent.id : null;
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
  /** The sites as the shelf read them. The record's own read belongs to the panel drawing it, so
   *  the address the frame stands on is taken from the listing the sheet was opened out of — which
   *  holds every site whatever page of files the shelf is walking. */
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

/** A file's name is what a member finds it by, so the column carrying it is measured from the rows
 *  rather than cut to a track: `q3-revenue-review.md` and `q3-revenue-rebuild.md` stand one record
 *  apart, and `q3-revenue-re…` names neither. */
const COLUMNS = [{ label: "Name", whole: true }, "Details", { label: "Type", fact: true }];

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
  if (card.image) return <img alt="" src={card.image} className="size-full object-cover" />;
  if (!card.file) return <IconWorldWww className="size-icon" aria-hidden />;
  return <MediaIcon mediaType={card.file.media_type} />;
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

mountApp(document.getElementById("root")!, (init) => (
  <SectionApp
    tab="artifacts"
    init={init}
    view={{
      label: "Artifacts",
      remountOnPlace: false,
      search: "Search artifacts",
      render: (place, onPlace) => <Artifacts place={place} onPlace={onPlace} />,
    }}
  />
));
