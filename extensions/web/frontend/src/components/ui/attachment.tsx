import { useEffect, useState, type ComponentProps, type ReactNode } from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/cn";

/** A file the conversation is carrying — one the member attached to a message, or one a reply
 *  produced. It is drawn as a card on the pane rather than as a line of text, so a message with
 *  files reads as words and then things, and it keeps a minimum width so a four-character name
 *  does not shrink the card to a pill the pointer has to hunt for. */
const attachmentVariants = cva(
  cn(
    "flex w-fit max-w-full min-w-(--container-attachment) shrink-0 flex-wrap items-center",
    "rounded-panel border border-edge bg-card text-card-foreground",
  ),
  {
    variants: {
      size: {
        sm: cn(
          "gap-md text-small",
          "has-data-[slot=attachment-content]:px-sm has-data-[slot=attachment-content]:py-xs",
        ),
      },
    },
    defaultVariants: { size: "sm" },
  },
);

export function Attachment({
  className,
  size = "sm",
  ...props
}: ComponentProps<"div"> & VariantProps<typeof attachmentVariants>) {
  return (
    <div
      data-slot="attachment"
      className={cn(attachmentVariants({ size }), className)}
      {...props}
    />
  );
}

export function AttachmentContent({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="attachment-content"
      className={cn("max-w-full min-w-0 flex-1 leading-chrome", className)}
      {...props}
    />
  );
}

/** The file's name, truncated rather than wrapped: the card holds one line, so a row of files
 *  stays one row tall whatever the names are. */
export function AttachmentTitle({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      data-slot="attachment-title"
      className={cn("block max-w-full min-w-0 truncate font-medium", className)}
      {...props}
    />
  );
}

export function AttachmentDescription({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      data-slot="attachment-description"
      className={cn(
        "mt-hair block max-w-full min-w-0 truncate text-small text-ink-soft",
        className,
      )}
      {...props}
    />
  );
}

/** A row of files that scrolls sideways rather than wrapping, so a message carrying six of them
 *  stays one row tall and the reply beneath it does not move down the page. The fade that says
 *  "more that way" ramps over the row's edges, so the ramp lives in gutters the padding opens and
 *  the negative margin gives back — over the first card, it would eat the card's left border. */
export function AttachmentGroup({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="attachment-group"
      className={cn(
        "-mx-sm flex min-w-0 gap-lg overflow-x-auto overscroll-x-contain px-sm py-2xs",
        "scroll-fade-x scrollbar-none snap-x snap-mandatory scroll-px-sm",
        "*:data-[slot=attachment]:flex-none *:data-[slot=attachment]:snap-start",
        "*:data-[slot=attachment-thumbnail]:snap-start",
        className,
      )}
      {...props}
    />
  );
}

const PDF_MEDIA_TYPE = "application/pdf";

/** What a card says it is a picture of, or nothing for a file that is its own picture. A rendered
 *  page is a picture of a document and reads as one only while something names the document type it
 *  came from; an image needs no label, because the label would be the picture. */
export function attachmentBadge(mediaType: string): string | null {
  return mediaType === PDF_MEDIA_TYPE ? "PDF" : null;
}

/** One file drawn as the thing it is rather than as its name: a thumbnail card holding the picture,
 *  cover-cropped, so a row of files reads as a row of pictures whatever shape each file is. A file
 *  with no picture — a type nothing renders, a picture the browser could not load — keeps the card
 *  and names itself inside it, so a member always sees one thing per file they attached and the row
 *  never collapses to nothing.
 *
 *  A preview link is answered off the conversation's live workspace, so it can stop answering while
 *  the bubble stays on the page: a failed load falls back to the named card in place, never to a
 *  broken frame. The fallback is held against the link that failed, so a fresh link drawn later
 *  draws again. */
export function AttachmentThumbnail({
  filename,
  previewUrl,
  mediaType,
  className,
  children,
}: {
  filename: string;
  previewUrl: string | null;
  mediaType: string;
  className?: string;
  /** What the card carries over its picture beside the badge — the act that takes the file back
   *  off the message being written. */
  children?: ReactNode;
}) {
  const [failed, setFailed] = useState<string | null>(null);
  const drawn = previewUrl !== null && failed !== previewUrl ? previewUrl : null;
  const badge = attachmentBadge(mediaType);
  return (
    <div
      data-slot="attachment-thumbnail"
      className={cn(
        "relative flex size-(--size-thumbnail) shrink-0 items-end",
        "overflow-hidden rounded-panel border border-edge bg-card text-card-foreground",
        className,
      )}
    >
      {drawn === null ? null : (
        <img
          loading="lazy"
          alt={filename}
          src={drawn}
          onError={() => setFailed(drawn)}
          className="absolute inset-0 size-full object-cover"
        />
      )}
      <div className="relative flex min-w-0 flex-1 items-end gap-xs p-sm">
        {badge === null ? null : <AttachmentBadge>{badge}</AttachmentBadge>}
        {drawn === null ? (
          <span className="min-w-0 flex-1 truncate text-small leading-chrome">{filename}</span>
        ) : null}
      </div>
      {children}
    </div>
  );
}

/** The chip over a card's lower-left corner naming the kind of file its picture came from. It keeps
 *  the card's own surface under it, so it reads against a page of text as well as against a
 *  photograph. */
export function AttachmentBadge({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      data-slot="attachment-badge"
      className={cn(
        "rounded-control border border-edge bg-card px-xs py-hair",
        "text-small leading-chrome font-medium text-card-foreground",
        className,
      )}
      {...props}
    />
  );
}

const PICKED_PICTURE_MAX_BYTES = 10 * 1024 * 1024;
const PICKED_PICTURE_TYPES = /^image\/(gif|jpeg|png|webp)$/;

/** The picture of a file the member has picked but not sent yet, read off the file itself — nothing
 *  on the server holds it while they are still writing the message.
 *
 *  It is read as a `data:` URL because the page's own policy admits those images and no `blob:` at
 *  all, and only for the raster types a browser draws: an SVG is a document that can carry script,
 *  and this page never draws one off bytes it was handed. A file over the ceiling is left as a named
 *  card rather than held in the page a second time as text. */
function usePickedPicture(file: File): string | null {
  const [picture, setPicture] = useState<string | null>(null);
  useEffect(() => {
    setPicture(null);
    if (!PICKED_PICTURE_TYPES.test(file.type) || file.size > PICKED_PICTURE_MAX_BYTES) return;
    const reader = new FileReader();
    reader.onload = () => setPicture(typeof reader.result === "string" ? reader.result : null);
    reader.readAsDataURL(file);
    return () => reader.abort();
  }, [file]);
  return picture;
}

/** One file in the member's hand, drawn as the card it will be drawn as once it is sent. */
export function PickedThumbnail({
  file,
  className,
  children,
}: {
  file: File;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <AttachmentThumbnail
      filename={file.name}
      previewUrl={usePickedPicture(file)}
      mediaType={file.type}
      className={className}
    >
      {children}
    </AttachmentThumbnail>
  );
}
