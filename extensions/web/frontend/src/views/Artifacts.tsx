import { useState } from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import { ArtifactText, isTextMedia } from "@/kernel/artifact";
import type { ListingSpec } from "@/kernel/listing";
import { cn } from "@/lib/cn";
import { ownerLabel, useViewer } from "@/lib/audience";
import { day } from "@/lib/moments";
import { formatSize } from "@/lib/size";
import { Sheet } from "@/components/ui/sheet";

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

type ArtifactsPayload = {
  artifacts: Artifact[];
  newer?: string | null;
  older?: string | null;
};

const MEDIA: Record<string, string> = {
  Images: "image",
  Documents: "document",
  Data: "data",
  Other: "other",
};

function isImage(entry: Artifact): boolean {
  return entry.media_type.startsWith("image/");
}

function isText(entry: Artifact): boolean {
  return isTextMedia(entry.media_type);
}

export const ARTIFACTS: ListingSpec<ArtifactsPayload, Artifact> = {
  read: "/workspace/artifacts",
  paged: true,
  serverQuery: true,
  query: (place) => {
    const params = new URLSearchParams();
    if (place.q) params.set("q", place.q);
    const media = MEDIA[place.chip ?? ""];
    if (media) params.set("media", media);
    return params;
  },
  rows: (payload) => payload.artifacts,
  rowKey: (entry) => entry.created_at + "|" + entry.filename,
  chips: [{ label: "Images" }, { label: "Documents" }, { label: "Data" }, { label: "Other" }],
  cards: {
    mark: { shape: "band", image: (entry) => (isImage(entry) ? entry.url : null) },
    primary: { field: "filename" },
    status: { field: "created_at", render: (stamp) => day(stamp) },
    body: { field: "subject" },
    meta: {
      field: "owner_email",
      render: (owner, _entry, context) => ownerLabel(owner, context.viewer),
    },
  },
  empty: "A file an agent shares from a conversation is listed here.",
  actions: (entry, { open }) =>
    entry.url ? (
      <Button variant="row" onClick={() => open(entry)}>
        View
      </Button>
    ) : null,
  detail: (entry, close) => <Viewer entry={entry} onClose={close} />,
};

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
      ) : isText(entry) ? (
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
      className="max-h-[var(--media-tall)] max-w-full object-contain"
    />
  );
}
