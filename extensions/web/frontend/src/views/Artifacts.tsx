import { useEffect, useState } from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import type { ListingSpec } from "@/kernel/listing";
import { cn } from "@/lib/cn";
import { day } from "@/lib/moments";
import { formatSize } from "@/views/Chat";
import { Sheet } from "@/components/ui/sheet";

const VIEWER_TEXT_BYTES = 64 * 1024;

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
  return entry.media_type.startsWith("text/") || entry.media_type === "application/json";
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
        <TextPreview url={entry.url} />
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

function TextPreview({ url }: { url: string | null }) {
  const [body, setBody] = useState<string | null>(null);
  const [bounded, setBounded] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!url) return;
    let live = true;
    (async () => {
      try {
        const res = await fetch(url, { credentials: "same-origin" });
        if (!res.ok) {
          if (live) setMessage("Error " + res.status + " — reload to retry.");
          return;
        }
        const reader = res.body!.getReader();
        const chunks: Uint8Array[] = [];
        let read = 0;
        let ended = false;
        while (!ended && read <= VIEWER_TEXT_BYTES) {
          const step = await reader.read();
          if (step.done) ended = true;
          else {
            chunks.push(step.value);
            read += step.value.length;
          }
        }
        if (!ended) await reader.cancel();
        const bytes = new Uint8Array(read);
        let at = 0;
        for (const chunk of chunks) {
          bytes.set(chunk, at);
          at += chunk.length;
        }
        if (!live) return;
        setBody(new TextDecoder().decode(bytes.slice(0, VIEWER_TEXT_BYTES)));
        setBounded(read > VIEWER_TEXT_BYTES);
      } catch {
        if (live) setMessage("Network error — try again.");
      }
    })();
    return () => {
      live = false;
    };
  }, [url]);

  if (message) return <div className="font-mono text-small opacity-(--muted)">{message}</div>;
  if (body === null) return <div>Loading…</div>;
  return (
    <>
      <pre className="m-0 max-h-[var(--media-tall)] overflow-x-auto whitespace-pre-wrap rounded-panel bg-fill-subtle p-lg font-mono text-mono [overflow-wrap:anywhere]">
        {body}
      </pre>
      {bounded ? (
        <div className="font-mono text-small opacity-(--muted)">
          First {formatSize(VIEWER_TEXT_BYTES)} shown.
        </div>
      ) : null}
    </>
  );
}
