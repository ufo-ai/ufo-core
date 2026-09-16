import { useState } from "react";

import {
  Attachment,
  AttachmentBadge,
  AttachmentContent,
  AttachmentDescription,
  AttachmentGroup,
  AttachmentThumbnail,
  AttachmentTitle,
  PickedThumbnail,
  attachmentBadgeFor,
} from "@/components/ui/attachment";
import { cn } from "@/lib/cn";
import { formatSize } from "@/lib/size";
import type { ChatFile } from "@/lib/types";

export type Opened = { files: ChatFile[]; at: number };

const TAP_FLOOR = "max-narrow:inline-flex max-narrow:min-h-(--size-control) max-narrow:items-center";

export function TurnFiles({
  files,
  onOpen,
}: {
  files: ChatFile[];
  onOpen: (opened: Opened) => void;
}) {
  const shared = files.filter((file) => file.role !== "details");
  const carried = files.filter((file) => file.role === "details");
  const images = shared.filter(
    (file) => file.media_type.startsWith("image/") && file.preview_url !== null,
  );
  const documents = shared.filter(
    (file) => !file.media_type.startsWith("image/") || file.preview_url === null,
  );
  return (
    <>
      {carried.map((file, index) => (
        <CarriedReport
          key={file.filename + String(index)}
          file={file}
          onOpen={() => onOpen({ files: [file], at: 0 })}
        />
      ))}
      {images.length === 1 ? (
        <FilePicture file={images[0]} onOpen={() => onOpen({ files: images, at: 0 })} />
      ) : null}
      {images.length > 1 ? (
        <AttachmentGroup className="mt-2xs items-start">
          {images.map((file, index) => (
            <FilePicture
              key={file.filename + String(index)}
              file={file}
              onOpen={() => onOpen({ files: images, at: index })}
              grouped
            />
          ))}
        </AttachmentGroup>
      ) : null}
      {documents.length ? (
        <div
          data-slot="attachment-grid"
          className="mt-2xs grid grid-cols-2 items-start gap-lg max-narrow:grid-cols-1"
        >
          {documents.map((file, index) => (
            <FileCard
              key={file.filename + String(index)}
              file={file}
              onOpen={() => onOpen({ files: [file], at: 0 })}
            />
          ))}
        </div>
      ) : null}
    </>
  );
}

export function CarriedReport({ file, onOpen }: { file: ChatFile; onOpen: () => void }) {
  if (!file.url) {
    return <span className="mt-2xs font-mono text-small text-ink-soft">{file.filename}</span>;
  }
  return (
    <button
      type="button"
      onClick={onOpen}
      title={file.filename}
      className={cn(
        "mt-2xs cursor-pointer self-start border-0 bg-transparent p-0 text-left text-link underline",
        TAP_FLOOR,
      )}
    >
      {file.subject || "Open detailed report"}
    </button>
  );
}

export function FileCard({ file, onOpen }: { file: ChatFile; onOpen: () => void }) {
  const thumbnail =
    file.preview_url === null ? null : (
      <AttachmentThumbnail filename={file.filename} previewUrl={file.preview_url} />
    );
  return (
    <Attachment size="sm" className={cn("w-full min-w-0", thumbnail && "flex-nowrap")}>
      {thumbnail === null ? null : (
        <button
          type="button"
          onClick={onOpen}
          aria-label={`Open ${file.filename}`}
          className="shrink-0 cursor-pointer border-0 bg-transparent p-0"
        >
          {thumbnail}
        </button>
      )}
      <AttachmentContent>
        <AttachmentTitle>
          <button
            type="button"
            onClick={onOpen}
            className={cn("cursor-pointer border-0 bg-transparent p-0 text-inherit", TAP_FLOOR)}
          >
            {file.filename}
          </button>
        </AttachmentTitle>
        {file.size_bytes === undefined ? null : (
          <AttachmentDescription>{formatSize(file.size_bytes)}</AttachmentDescription>
        )}
      </AttachmentContent>
    </Attachment>
  );
}

export function AttachedFiles({
  files,
  picked,
  onOpen,
}: {
  files: ChatFile[];
  picked: File[];
  onOpen: (opened: Opened) => void;
}) {
  if (!picked.length && !files.length) return null;
  return (
    <AttachmentGroup className="mb-2xs justify-end">
      {picked.length
        ? picked.map((file, at) => <PickedThumbnail key={file.name + String(at)} file={file} />)
        : files.map((file, at) => (
            <button
              key={file.filename}
              type="button"
              onClick={() => onOpen({ files, at })}
              aria-label={`Open ${file.filename}`}
              className="cursor-pointer border-0 bg-transparent p-0"
            >
              <AttachmentThumbnail filename={file.filename} previewUrl={file.preview_url} />
            </button>
          ))}
    </AttachmentGroup>
  );
}

export function FilePicture({
  file,
  onOpen,
  grouped = false,
}: {
  file: ChatFile;
  onOpen: () => void;
  grouped?: boolean;
}) {
  const [failed, setFailed] = useState<string | null>(null);
  const badge = attachmentBadgeFor(file.filename);
  const className = cn(
    "w-fit",
    grouped ? "max-w-full shrink-0 snap-start" : "mt-2xs",
  );
  const drawn =
    failed === file.preview_url ? (
      <AttachmentThumbnail filename={file.filename} previewUrl={null} />
    ) : (
      <span className="relative block w-fit">
        <img
          loading="lazy"
          alt={file.filename}
          src={file.preview_url ?? undefined}
          onError={() => setFailed(file.preview_url)}
          className="max-h-(--media-card) max-w-full rounded-panel border border-edge object-contain"
        />
        {badge === null ? null : (
          <AttachmentBadge className="absolute bottom-0 left-0 m-sm">{badge}</AttachmentBadge>
        )}
      </span>
    );
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(className, "cursor-pointer border-0 bg-transparent p-0")}
    >
      {drawn}
    </button>
  );
}
