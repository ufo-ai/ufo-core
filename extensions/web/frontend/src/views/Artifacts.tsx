import { useState } from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import { ArtifactText, isTextMedia } from "@/kernel/artifact";
import type { ListingSpec } from "@/kernel/listing";
import { cn } from "@/lib/cn";
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
};

type ArtifactsPayload = {
  artifacts: Artifact[];
  newer?: string | null;
  older?: string | null;
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
  rows: (payload) => payload.artifacts,
  rowKey: (entry) => entry.created_at + "|" + entry.filename,
  search: (entry) => [entry.filename, entry.subject ?? "", entry.media_type].join(" "),
  cards: {
    mark: { shape: "band", image: (entry) => (isImage(entry) ? entry.url : null) },
    primary: { field: "filename" },
    status: { field: "created_at", render: (stamp) => day(stamp) },
    body: { field: "subject" },
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
  const meta = [entry.subject, entry.media_type, formatSize(entry.size_bytes), day(entry.created_at)]
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
        <ArtifactText url={entry.url} />
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
