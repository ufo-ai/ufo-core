import { useState, type ReactNode } from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import { Filter, Segmented } from "@/components/ui/filter";
import { Sheet } from "@/components/ui/sheet";
import { ArtifactText, isTextMedia } from "@/kernel/artifact";
import { CardGrid } from "@/kernel/cards";
import {
  OWNER_FIELD,
  ObjectDetail,
  creator,
  type ObjectAddress,
  type ObjectRow,
} from "@/kernel/objects";
import { Pager, type Placement } from "@/kernel/pager";
import { PageToolbar } from "@/kernel/pane";
import {
  Panel,
  PanelBlank,
  PanelEmpty,
  Section,
  usePanelRead,
  type PanelState,
} from "@/kernel/panel";
import { ownerLabel, slackLink, useViewer } from "@/lib/audience";
import { cn } from "@/lib/cn";
import { useMainAgent } from "@/lib/mainAgent";
import { day } from "@/lib/moments";
import { chatHash } from "@/lib/route";
import { formatSize } from "@/lib/size";

const SITE_KIND = "site";
const CURSOR_SEPARATOR = "|";

const CURSOR_OLDER = "older";

const OBJECT_PREFIX = "object/";
const NOT_FOUND = 404;
const SITE_FAMILY = "Sites";
const MEDIA: Record<string, string> = {
  Images: "image",
  Documents: "document",
  Data: "data",
  Other: "other",
};
const FAMILIES = [SITE_FAMILY, ...Object.keys(MEDIA)];

type Scope = "created" | "shared" | "all";

const SCOPE_KEY = "artifacts-scope";
const SCOPE_SEGMENTS = [
  { label: "All", value: "all" },
  { label: "Created by me", value: "created" },
  { label: "Shared with me", value: "shared" },
];

function asScope(value: string | null): Scope {
  return value === "created" || value === "shared" ? value : "all";
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
 *  sorts last rather than sorting arbitrarily. */
type Card = {
  key: string;
  name: string;
  time: number;
  status: string | null;
  body: string | null;
  meta: string;
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
    status: day(createdAt),
    body: typeof row.summary === "string" ? row.summary : null,
    meta: [
      "Site",
      typeof row.visibility === "string" ? visibilityLabel(row.visibility) : null,
      creator(row[OWNER_FIELD], viewer),
    ]
      .filter((part) => part)
      .join(" · "),
    image: null,
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

/** A shaped text file's band is its fingerprint: the content renders at reading width — markdown
 *  as the document it is, a csv as its table, json and a patch as their characters — and scales to
 *  the edge of legibility, so the band holds a dense picture of the page rather than one
 *  full-sized opening line. It reads the same link the viewer reads whole, the one preview an
 *  already-shared file can grow without a re-render; plain text stays pictureless, since it has no
 *  shape a fingerprint would carry. The fingerprint is `inert`: the band is decoration, so nothing
 *  in it takes a press or the keyboard, and the band's own overflow crops it. */
function bandExcerpt(file: Artifact | null): ReactNode {
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
    status: day(entry.created_at),
    body: entry.subject,
    meta: ownerLabel(entry.owner_email, viewer),
    image: entry.preview_url ?? (isImage(entry) ? entry.url : null),
    link: null,
    file: entry,
  };
}

/** The files walk is the shelf's own read: it fails the section, and it is the one page a cursor
 *  continues. A deploy with no sites extension answers the site read with a 404, which is a family
 *  that does not exist here rather than a fault — every other refusal is stated.
 *
 *  The sites arrive whole on every read, so a page keeps only the ones made inside its own window,
 *  and the windows have to tile: a site between the oldest file of one page and the newest file of
 *  the next belongs to exactly one of them, never to neither. The foot of the window is this page's
 *  oldest file and the top is the position the cursor names — the last row of the page above, which
 *  is that page's foot — so the two meet on one value and a record at it stands on the upper page
 *  alone. The first page is open at the top and the last is open at the foot. */
/** The moment a page starts under, read from the cursor that opened it: `side|created_at|item_id`.
 *  Only a cursor walking older names this — it is the last row of the page above, so this page
 *  holds what is older than it. A cursor walking newer names the row *below* this page, which
 *  bounds its foot and says nothing about its top; read as a top it would put the whole window
 *  under this page's own oldest file and leave no site standing anywhere. A page whose top is
 *  unnamed — the newest page, or one walked back into from below — is open at the top, so a site
 *  can stand on two pages while the member walks backwards but can never stand on none. */
function startsUnder(after: string | undefined): number {
  const [side, stamp] = (after ?? "").split(CURSOR_SEPARATOR);
  const at = Date.parse(stamp ?? "");
  return side === CURSOR_OLDER && !Number.isNaN(at) ? at : Number.POSITIVE_INFINITY;
}

function shelf(
  sites: PanelState<SitesPayload>,
  files: PanelState<FilesPayload>,
  viewer: string | null,
  after: string | undefined,
  scope: Scope,
): PanelState<Shelf> {
  if (files.phase !== "ready") return files;
  if (sites.phase === "loading") return sites;
  if (sites.phase === "failed" && sites.status !== NOT_FOUND) return sites;
  const artifacts = files.payload.artifacts;
  const oldestFile = Math.min(...artifacts.map((entry) => Date.parse(entry.created_at)));
  const older = Boolean(files.payload.older);
  const under = startsUnder(after);
  const standing =
    sites.phase === "ready"
      ? sites.payload.objects.filter((row) => {
          if (scope !== "all") {
            const owned = viewer !== null && row[OWNER_FIELD] === viewer;
            if ((scope === "created") !== owned) return false;
          }
          const at = moment(typeof row.created_at === "string" ? row.created_at : null);
          return at < under && (!older || at >= oldestFile);
        })
      : [];
  const cards = [
    ...standing.map((row) => siteCard(row, viewer)),
    ...artifacts.map((entry) => fileCard(entry, viewer)),
  ];
  cards.sort((left, right) => (left.time < right.time ? 1 : left.time > right.time ? -1 : 0));
  return {
    phase: "ready",
    payload: {
      cards,
      files: files.payload,
    },
  };
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
  const [scope, setScope] = useState<Scope>(() => asScope(localStorage.getItem(SCOPE_KEY)));
  const holdScope = (value: string) => {
    const next = asScope(value);
    setScope(next);
    localStorage.setItem(SCOPE_KEY, next);
    onPlace({ after: undefined });
  };
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
  const state = shelf(
    mainAgent && !media ? held : NO_SITES,
    picked === SITE_FAMILY ? NO_FILES : walked,
    viewer,
    place.after,
    scope,
  );

  const at = objectAt(place.open);
  if (at !== null && at.name !== null && mainAgent)
    return (
      <ObjectDetail
        key={at.kind + "/" + at.name}
        agentId={mainAgent.id}
        kind={at.kind}
        name={at.name}
        onOpen={(next) => onPlace({ open: OBJECT_PREFIX + next.kind + "/" + (next.name ?? "") })}
        onBack={() => onPlace({ open: undefined })}
      />
    );

  const unknown = Boolean(picked) && !FAMILIES.includes(picked);
  const absent = held.phase === "failed" && held.status === NOT_FOUND;
  const families = absent ? Object.keys(MEDIA) : FAMILIES;

  return (
    <>
      {state.phase === "loading" ? null : (
        <PageToolbar>
          <Segmented label="Scope" segments={SCOPE_SEGMENTS} value={scope} onPick={holdScope} />
          <Filter
            options={families.map((family) => ({ label: family, value: family }))}
            value={picked}
            onChange={(value) => onPlace({ chip: value || undefined, after: undefined })}
          />
          {state.phase === "failed" && place.after ? (
            <Button
              variant="row"
              onClick={() => onPlace({ after: undefined, open: undefined })}
            >
              First page
            </Button>
          ) : null}
        </PageToolbar>
      )}
      <Section>
      <Panel state={state} shape="cards">
        {(payload) => {
          if (unknown) return <PanelBlank body="That filter is not available." />;
          if (!payload.cards.length)
            return (
              <PanelBlank
                body={
                  query || picked
                    ? "Nothing matches."
                    : "A file or site an agent makes in a conversation is listed here."
                }
              />
            );
          const opened = payload.cards.find((card) => card.key === place.open);
          return (
            <>
              <CardGrid
                rows={payload.cards}
                rowKey={(card) => card.key}
                mark={{
                  shape: "band",
                  image: (card) => card.image,
                  body: (card) => bandExcerpt(card.file),
                }}
                primary={(card) => card.name}
                status={(card) => card.status}
                body={(card) => card.body}
                meta={(card) => card.meta}
                action={(card) => <Act card={card} onPlace={onPlace} />}
              />
              {picked === SITE_FAMILY ? null : (
                <Pager payload={payload.files} onPlace={onPlace} />
              )}
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
    </>
  );
}

/** A site's own link leaves the portal and leads the foot; `View` opens the record in the pane.
 *  A file carries only `View`, and only once its link is minted. */
function Act({ card, onPlace }: { card: Card; onPlace: (place: Placement) => void }) {
  const view =
    !card.file || card.file.url ? (
      <Button variant="row" onClick={() => onPlace({ open: card.key })}>
        View
      </Button>
    ) : null;
  if (!card.link) return view;
  return (
    <div className="flex flex-wrap gap-xs">
      <a
        href={card.link}
        target="_blank"
        rel="noopener noreferrer"
        className={cn(buttonVariants({ variant: "send" }), "no-underline")}
      >
        Open
      </a>
      {view}
    </div>
  );
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
    day(entry.created_at),
  ]
    .filter((part) => part)
    .join(" · ");

  return (
    <Sheet
      open
      onClose={onClose}
      title={entry.filename}
      footer={
        entry.url ? (
          <a
            href={entry.url}
            download={entry.filename}
            className={cn(buttonVariants({ variant: "send" }), "inline-block no-underline")}
          >
            Download
          </a>
        ) : null
      }
    >
      <div className="font-mono text-small text-ink-soft">{meta}</div>
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
        <ArtifactText url={entry.url} name={entry.filename} mediaType={entry.media_type} />
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
