// The artifacts app's page: TSX compiled in the browser by the portal's app kit. Everything it
// composes comes off the UfoAppKit global; edit this file and redeploy to change the page.

const {
  ArtifactText,
  Button,
  CardGrid,
  DataTable,
  FacetMenu,
  IconWorldWww,
  Lede,
  MediaIcon,
  Moment,
  OWNER_FIELD,
  ObjectDetail,
  PageToolbar,
  Pager,
  Panel,
  PanelBlank,
  PanelEmpty,
  React,
  Section,
  SectionApp,
  Segmented,
  Td,
  TdFact,
  ToolbarRule,
  ViewSwitch,
  appended,
  beside,
  buttonVariants,
  chatHash,
  closed,
  cn,
  creator,
  formatSize,
  isTextMedia,
  mountApp,
  objectAt,
  opened,
  ownerLabel,
  slackLink,
  slotOf,
  useCallback,
  useMainAgent,
  usePanelRead,
  useRef,
  useSlot,
  useState,
  useViewer,
} = UfoAppKit;

const SITE_KIND = "site";

const NOT_FOUND = 404;
/** What the shelf is called where a lane has to say where it was opened from. */
const SHELF = "Artifacts";
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
  id: string;
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

/** A shared file's lane id: the file's own id, which is what the shelf keys its card by and what
 *  the address and the track store carry. A filename is whatever the agent that shared it called
 *  the file — free text of any length, holding the characters a track is written with — so a lane
 *  named off it is a lane the address cannot always carry. The spotlight mints the same id from
 *  the same fact, which is what makes a hit open the card this shelf lists. */
function fileKey(file: { id: string }): string {
  return file.id;
}

function fileCard(entry: Artifact, viewer: string | null): Card {
  return {
    key: fileKey(entry),
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
  /** The app the sites were read under, which is the app their lanes name; null where the shelf
   *  read no sites at all. */
  owner: string | null,
): PanelState<Shelf> {
  if (files.phase !== "ready") return files;
  if (sites.phase === "loading") return sites;
  if (sites.phase === "failed" && sites.status !== NOT_FOUND) return sites;
  const shown =
    owner !== null && sites.phase === "ready" && (onSites || !files.payload.newer)
      ? sites.payload.objects
          .filter((row) => scope === "all" || (viewer !== null && row[OWNER_FIELD] === viewer))
          .map((row) => siteCard(row, viewer, owner))
      : [];
  const cards = [...shown, ...files.payload.artifacts.map((entry) => fileCard(entry, viewer))];
  cards.sort((left, right) => (left.time < right.time ? 1 : left.time > right.time ? -1 : 0));
  return { phase: "ready", payload: { cards, files: files.payload } };
}

/** What a lane of the track is called: a record by the name in its own id, a file by the card it
 *  was opened from — and nothing at all while the shelf it came off has not answered. */
function nameOf(id: string, cards: Card[] | null): string | undefined {
  return objectAt(id)?.name ?? cards?.find((card) => card.key === id)?.name;
}

/** What each press asked for, read off the press itself: a tile and a table row both hand their
 *  opener a bare call, so the gesture would otherwise be gone by the time the record is opened. One
 *  listener over the records, in the capture phase, so it is recorded before the row acts on it.
 *
 *  The middle button raises no press of its own on a button or a row, so the shelf raises the press
 *  it would have been. A press that landed on a link is that link's: a site's own address opens in
 *  the browser's new tab exactly as it does anywhere else. */
const MIDDLE_BUTTON = 1;

function Presses({ alongside, children }: { alongside: RefObject<boolean>; children: ReactNode }) {
  return (
    <div
      className="contents"
      onMouseDownCapture={(event) => {
        alongside.current = beside(event);
      }}
      onKeyDownCapture={(event) => {
        alongside.current = beside(event);
      }}
      onAuxClickCapture={(event) => {
        if (event.button !== MIDDLE_BUTTON) return;
        const pressed = event.target as HTMLElement;
        if (pressed.closest("a")) return;
        pressed.closest<HTMLElement>("button, tr")?.click();
      }}
    >
      {children}
    </div>
  );
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
  const alongside = useRef(false);
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
  /** The app the sites are read under, which is the app their lanes name. A shelf narrowed to one
   *  media kind lists files alone, and one with no main agent has read no sites at all. */
  const siteOwner = mainAgent && !media ? mainAgent.id : null;
  const state = shelf(
    siteOwner ? held : NO_SITES,
    onSites ? NO_FILES : walked,
    viewer,
    onSites,
    scope,
    siteOwner,
  );

  const opens = place.opens ?? [];
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
          {state.phase === "failed" && place.after ? (
            <Button variant="row" onClick={() => onPlace({ after: undefined, opens: undefined })}>
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
            const open = (card: Card) => () =>
              onPlace({
                opens: alongside.current ? appended(opens, card.key) : opened(opens, card.key),
              });
            return (
              <>
                <Presses alongside={alongside}>
                  {face === "table" ? (
                    <Shapes cards={payload.cards} opens={opens} open={open} />
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
                      current={(card) => opens.includes(card.key)}
                      action={(card) => (card.link ? <OpenSite href={card.link} /> : null)}
                    />
                  )}
                </Presses>
                {onSites ? null : <Pager payload={payload.files} onPlace={onPlace} />}
              </>
            );
          }}
        </Panel>
        {opens.map((id, at) => (
          <Opened
            key={id}
            id={id}
            cards={cards}
            sites={sites}
            from={(at === 0 ? undefined : nameOf(opens[at - 1], cards)) ?? SHELF}
            onOpen={(next) => onPlace({ opens: opened(opens, next, id) })}
            onClose={() => onPlace({ opens: closed(opens, id) })}
          />
        ))}
      </Section>
    </>
  );
}

/** One slot of the track. The shelf holds two families under one address, so the id itself decides
 *  what stands in the slot and how wide it is: a site is a record of a few fields and takes a panel,
 *  a file is read whole and takes a reading slot, where two files divide the width and are compared
 *  side by side. An outbound link inside a record opens the next slot beside this one; an id naming
 *  no file this page holds still stands as a lane and says so inside it, because the close is the
 *  lane's and an address the member cannot shut is one they cannot leave.
 *
 *  A site's lane leads with the site: the record states the facts, and the page itself is the one
 *  thing about it those facts cannot carry.
 *
 *  `from` is what this lane was opened out of — the lane standing to its left, or the shelf
 *  itself — said as the crumb over it, which is the way back a lane paged one to a screen has
 *  instead of the lane that would otherwise stand beside it. */
function Opened({
  id,
  cards,
  sites,
  from,
  onOpen,
  onClose,
}: {
  id: string;
  /** The shelf as it stands, or nothing while it is still being read — a file's slot waits for the
   *  page it is on rather than reporting the file missing from a listing that has not answered. */
  cards: Card[] | null;
  /** The sites as the shelf read them. The record's own read belongs to the panel drawing it, so
   *  the address the frame stands on is taken from the listing this lane was opened out of — which
   *  holds every site whatever page of files the shelf is walking. */
  sites: ObjectRow[] | null;
  from: string;
  onOpen: (id: string) => void;
  onClose: () => void;
}) {
  const at = objectAt(id);
  const card = cards?.find((entry) => entry.key === id) ?? null;
  const stranded = at === null && cards !== null && !card?.file;
  const site =
    at !== null && at.kind === SITE_KIND ? sites?.find((row) => row.name === at.name) : undefined;
  const url = typeof site?.site_url === "string" ? site.site_url : null;
  return useSlot(
    at !== null ? (
      <ObjectDetail
        agentId={at.agent}
        kind={at.kind}
        name={at.name}
        lead={url ? <SiteView url={url} name={at.name} /> : undefined}
        onOpen={(next) => onOpen(slotOf(next))}
        onBack={onClose}
      />
    ) : card?.file ? (
      <Viewer entry={card.file} />
    ) : stranded ? (
      <PanelEmpty>That item is not on this page.</PanelEmpty>
    ) : null,
    {
      id,
      kind: at !== null ? "panel" : "reading",
      title: stranded ? id : nameOf(id, cards),
      parent: { label: from, onGo: onClose },
      onClose,
    },
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
 *  column of marks stays a column rather than a run of gaps.
 *
 *  A record whose document is standing in the track is marked on the row band it stands in, the
 *  same mark the tiles carry: a press shuts the lanes to the right of the one it was taken in, and
 *  the mark is what makes that read as a path being walked rather than as slots leaving. */
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
      rows={cards}
      rowKey={(card) => card.key}
      empty="A file or site an app makes in a conversation is listed here."
      open={(card) => (card.file && !card.file.url ? null : open(card))}
      current={(card) => opens.includes(card.key)}
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

/** A file as the member reads it, standing in the track beside the shelf it was picked from rather
 *  than over it: the listing stays legible while the file is read, and a second file opens next to
 *  the first instead of taking its place. The slot's own header names the file and carries the way
 *  to shut it, so the body carries the facts, the ways back to where the file came from, and the
 *  file itself. */
function Viewer({ entry }: { entry: Artifact }) {
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
    <div className="flex min-h-0 flex-1 flex-col gap-2xl overflow-y-auto px-2xl pb-2xl">
      <div className="font-mono text-small text-ink-soft">
        {meta} · <Moment at={entry.created_at} />
      </div>
      <div className="flex flex-wrap items-center gap-lg">
        {entry.url ? (
          <a
            href={entry.url}
            download={entry.filename}
            className={cn(buttonVariants({ variant: "send" }), "shrink-0 no-underline")}
          >
            Download
          </a>
        ) : null}
        <span className="flex flex-wrap gap-x-lg font-mono text-small text-ink-soft">
          <a href={chatHash(entry.conversation_id)} className={out}>
            Conversation
          </a>
          {thread ? (
            <a href={thread} target="_blank" rel="noopener noreferrer" className={out}>
              Slack <span aria-hidden>↗</span>
            </a>
          ) : null}
        </span>
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
    </div>
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
