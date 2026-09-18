import { useState } from "react";

import { IconChevronRight } from "@tabler/icons-react";

import {
  AttachmentGroup,
  AttachmentThumbnail,
  FileMark,
  PickedThumbnail,
} from "@/components/ui/attachment";

import { Card } from "@/components/ui/card";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemMedia,
  ItemPress,
  ItemTitle,
  MarkTile,
} from "@/components/ui/item";
import { cn } from "@/lib/cn";
import { formatSize } from "@/lib/size";
import type { ChatFile } from "@/lib/types";

export type Opened = { files: ChatFile[]; at: number };

export function TurnFiles({
  files,
  onOpen,
}: {
  files: ChatFile[];
  onOpen: (opened: Opened) => void;
}) {
  const images = files.filter(
    (file) => file.role !== "details" && file.media_type.startsWith("image/") && file.preview_url !== null,
  );
  /* A file the answer linked is named by the words that linked it, and stands first: the reply
     speaks of it, so it is what the member looks for. */
  const documents = [
    ...files.filter((file) => file.role === "details"),
    ...files.filter(
      (file) =>
        file.role !== "details" && (!file.media_type.startsWith("image/") || file.preview_url === null),
    ),
  ];
  return (
    <>
      {images.length === 1 ? (
        <FilePicture file={images[0]} onOpen={() => onOpen({ files: images, at: 0 })} />
      ) : null}
      {images.length > 1 ? (
        <AttachmentGroup className="mt-sm items-start">
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
        <div className="mt-sm flex max-w-said flex-col gap-sm">
          {documents.map((file, index) => (
            <Card key={file.filename + String(index)} rows>
              <ItemGroup>
                <FileRow file={file} onOpen={() => onOpen({ files: [file], at: 0 })} />
              </ItemGroup>
            </Card>
          ))}
        </div>
      ) : null}
    </>
  );
}

function FileRow({ file, onOpen }: { file: ChatFile; onOpen: () => void }) {
  const inside = (
    <>
      <ItemMedia>
        <MarkTile compact>
          <FileMark filename={file.filename} />
        </MarkTile>
      </ItemMedia>
      <ItemContent>
        <ItemTitle>{file.subject || file.filename}</ItemTitle>
        <ItemDescription>{describe(file)}</ItemDescription>
      </ItemContent>
    </>
  );
  /* `_file_payload` mints both links off the deploy's artifact secret and public base URL, so a
     deploy holding neither names every file it sends without an address of any kind. */
  if (file.url === null && file.preview_url === null) {
    return <Item size="row">{inside}</Item>;
  }
  return (
    <Item size="flush">
      <ItemPress onPress={onOpen}>
        {inside}
        <ItemActions>
          <IconChevronRight aria-hidden className="size-icon shrink-0 text-ink-soft" />
        </ItemActions>
      </ItemPress>
    </Item>
  );
}

/** What stands under a document's name: the file it is where the answer named it something else,
 *  and its weight where the wire carried one. */
function describe(file: ChatFile): string {
  const size = file.size_bytes === undefined ? null : formatSize(file.size_bytes);
  const named = file.subject ? file.filename : null;
  return [named, size].filter(Boolean).join(" · ");
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
  const className = cn(
    "w-fit",
    grouped ? "max-w-full shrink-0 snap-start" : "mt-sm",
  );
  const drawn =
    failed === file.preview_url ? (
      <AttachmentThumbnail filename={file.filename} previewUrl={null} />
    ) : (
      <img
        loading="lazy"
        alt={file.filename}
        src={file.preview_url ?? undefined}
        onError={() => setFailed(file.preview_url)}
        className="max-h-(--media-card) max-w-full rounded-panel border border-edge object-contain"
      />
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
