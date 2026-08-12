import { useEffect, useState, type FormEvent } from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { Filter } from "@/components/ui/filter";
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
import {
  Panel,
  PanelBlank,
  PanelEmpty,
  Section,
  usePanelRead,
  type PanelState,
} from "@/kernel/panel";
import { ownerLabel, useViewer } from "@/lib/audience";
import { cn } from "@/lib/cn";
import { useMainAgent } from "@/lib/mainAgent";
import { day } from "@/lib/moments";
import { formatSize } from "@/lib/size";

const SITE_KIND = "site";
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

type Artifact = {
  filename: string;
  subject: string | null;
  media_type: string;
  size_bytes: number;
  created_at: string;
  url: string | null;
  owner_email: string | null;
  origin: string | null;
  conversation_id: string;
};

type FilesPayload = {
  artifacts: Artifact[];
  newer?: string | null;
  older?: string | null;
};

type SitesPayload = { objects: ObjectRow[] };

/** One card of the shelf. A site and a shared file are two families of the one thing the member
 *  came for — what the agents produced — so they are read down one grid, the small named family
 *  ahead of the directory it leads. */
type Card = {
  key: string;
  name: string;
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

function siteCard(row: ObjectRow, viewer: string | null): Card {
  return {
    key: OBJECT_PREFIX + SITE_KIND + "/" + row.name,
    name: row.name,
    status: typeof row.visibility === "string" ? visibilityLabel(row.visibility) : null,
    body: typeof row.summary === "string" ? row.summary : null,
    meta: creator(row[OWNER_FIELD], viewer),
    image: null,
    link: typeof row.site_url === "string" ? row.site_url : null,
    file: null,
  };
}

function fileCard(entry: Artifact, viewer: string | null): Card {
  return {
    key: entry.created_at + "|" + entry.filename,
    name: entry.filename,
    status: day(entry.created_at),
    body: entry.subject,
    meta: ownerLabel(entry.owner_email, viewer),
    image: isImage(entry) ? entry.url : null,
    link: null,
    file: entry,
  };
}

/** The files walk is the shelf's own read: it fails the section, and it is the one page a cursor
 *  continues. A deploy with no sites extension answers the site read with a 404, which is a family
 *  that does not exist here rather than a fault — every other refusal is stated. */
function shelf(
  sites: PanelState<SitesPayload>,
  files: PanelState<FilesPayload>,
  viewer: string | null,
): PanelState<Shelf> {
  if (files.phase !== "ready") return files;
  if (sites.phase === "loading") return sites;
  if (sites.phase === "failed" && sites.status !== NOT_FOUND) return sites;
  const standing = sites.phase === "ready" ? sites.payload.objects : [];
  return {
    phase: "ready",
    payload: {
      cards: [
        ...standing.map((row) => siteCard(row, viewer)),
        ...files.payload.artifacts.map((entry) => fileCard(entry, viewer)),
      ],
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
  const query = place.q ?? "";
  const picked = place.chip ?? "";
  const [typed, setTyped] = useState(query);
  useEffect(() => setTyped(query), [query]);

  const media = MEDIA[picked];
  const siteParams = new URLSearchParams({ agent: mainAgent?.id ?? "", order_by: "name" });
  if (query) siteParams.set("q", query);
  const held = usePanelRead<SitesPayload>(
    mainAgent ? "/objects/" + SITE_KIND + "?" + siteParams.toString() : null,
  );
  const fileParams = new URLSearchParams();
  if (query) fileParams.set("q", query);
  if (media) fileParams.set("media", media);
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

  function submit(event: FormEvent) {
    event.preventDefault();
    onPlace({ q: typed || undefined, after: undefined });
  }

  const unknown = Boolean(picked) && !FAMILIES.includes(picked);
  const absent = held.phase === "failed" && held.status === NOT_FOUND;
  const families = absent ? Object.keys(MEDIA) : FAMILIES;

  return (
    <Section
      title="Artifacts"
      bar={
        state.phase === "loading" ? null : (
          <>
            <form onSubmit={submit} className="flex items-stretch">
              <Input
                type="search"
                aria-label="Search"
                placeholder="Search"
                className="max-w-control-row"
                value={typed}
                onChange={(event) => setTyped(event.target.value)}
              />
            </form>
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
          </>
        )
      }
    >
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
                mark={{ shape: "band", image: (card) => card.image }}
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
      <div className="font-mono text-small opacity-(--muted)">{meta}</div>
      {isImage(entry) ? (
        <FullImage entry={entry} />
      ) : isTextMedia(entry.media_type) ? (
        <ArtifactText url={entry.url} mediaType={entry.media_type} />
      ) : (
        <div className="font-mono text-small opacity-(--muted)">
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
      <div className="font-mono text-small opacity-(--muted)">
        The image did not load. Its link may have expired — reload the listing.
      </div>
    );
  }
  return (
    <img
      alt={entry.subject || entry.filename}
      src={entry.url ?? ""}
      onError={() => setFailed(true)}
      className="max-h-(--media-tall) max-w-full object-contain"
    />
  );
}
