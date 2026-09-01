import { useEffect, useState, type ComponentProps, type ReactNode } from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { BASE } from "@/lib/api";
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

const ATTACHMENT_BADGES: Record<string, string> = {
  pdf: "PDF",
  docx: "DOCX",
  xlsx: "XLSX",
  pptx: "PPTX",
  csv: "CSV",
  md: "MD",
  svg: "SVG",
  mp4: "MP4",
  mov: "MOV",
  webm: "WEBM",
  mkv: "MKV",
};
const DOCUMENT_PREVIEW_TYPES = /\.(pdf|docx|xlsx|pptx|csv|md|svg)$/i;

/** What a card says it is a picture of, or nothing for a file that is its own picture. A rendered
 *  page or a video's first frame is a picture of a document and reads as one only while something
 *  names the kind of file it came from; the name carries that where the browser's media type does
 *  not (empty for `.md`, inconsistent for office types), so the badge is read off the extension. An
 *  image needs no label, because the label would be the picture — a raster type maps to nothing. */
export function attachmentBadgeFor(filename: string): string | null {
  const dot = filename.lastIndexOf(".");
  const ext = dot === -1 ? "" : filename.slice(dot + 1).toLowerCase();
  return ATTACHMENT_BADGES[ext] ?? null;
}

/** One file drawn as the thing it is rather than as its name: a thumbnail card holding the picture,
 *  cover-cropped, so a row of files reads as a row of pictures whatever shape each file is. A
 *  document's first page aligns to the top, so the crop removes only its bottom. A file with no
 *  picture — a type nothing renders, a picture the browser could not load — keeps the card and
 *  names itself inside it, so a member always sees one thing per file they attached and the row
 *  never collapses to nothing.
 *
 *  A preview link is answered off the conversation's live workspace, so it can stop answering while
 *  the bubble stays on the page: a failed load falls back to the named card in place, never to a
 *  broken frame. The fallback is held against the link that failed, so a fresh link drawn later
 *  draws again. */
export function AttachmentThumbnail({
  filename,
  previewUrl,
  loading = false,
  className,
  children,
}: {
  filename: string;
  previewUrl: string | null;
  /** The picture is still being rendered — draw a shimmer over the card so the member sees a
   *  preview is coming rather than a bare name that may or may not gain one. */
  loading?: boolean;
  className?: string;
  /** What the card carries over its picture beside the badge — the act that takes the file back
   *  off the message being written. */
  children?: ReactNode;
}) {
  const [failed, setFailed] = useState<string | null>(null);
  const drawn = previewUrl !== null && failed !== previewUrl ? previewUrl : null;
  const shimmering = loading && drawn === null;
  const badge = attachmentBadgeFor(filename);
  const documentPreview = DOCUMENT_PREVIEW_TYPES.test(filename);
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
          className={cn(
            "absolute",
            documentPreview
              ? "inset-x-0 top-0 h-auto w-full"
              : "inset-0 size-full object-cover",
          )}
        />
      )}
      {shimmering ? (
        <div
          data-slot="attachment-shimmer"
          aria-hidden
          className="absolute inset-0 animate-pulse bg-muted"
        />
      ) : null}
      <div className="relative flex min-w-0 flex-1 items-end gap-xs p-sm">
        {badge === null ? null : <AttachmentBadge>{badge}</AttachmentBadge>}
        {drawn === null && !shimmering ? (
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
// One ceiling for every file the preview route renders, document and video alike. The route refuses
// a body over MAX_REQUEST_BYTES (25 MB, `preview` in ufo_ext_web/surface.py), so a file admitted
// over that is uploaded whole only to earn a 413 and fall back to a bare name.
const PICKED_RENDER_MAX_BYTES = 25 * 1024 * 1024;
const PICKED_VIDEO_TYPES = /\.(mp4|mov|webm|mkv)$/i;

/** The picture of a file the member has picked but not sent yet.
 *
 *  A raster image is read straight off the file into a `data:` URL — the page's policy admits those
 *  and no `blob:` at all, and a browser draws them safely. A document (pdf, office, csv, md, svg) or
 *  a video (mp4, mov, webm, mkv) is sent to the preview service, which renders its first page — or a
 *  video's first frame — to a PNG and hands it straight back; the page draws that PNG, never the
 *  file's own bytes, so even an SVG — a document that can carry script — is safe here because the
 *  page never draws it, only the raster the service made of it. Anything else, or a file over its
 *  ceiling, is left as a named card. Nothing is stored while the member is still writing the
 *  message. */
type PickedPicture = { preview: string | null; loading: boolean };

function usePickedPicture(file: File): PickedPicture {
  const [state, setState] = useState<PickedPicture>({ preview: null, loading: false });
  useEffect(() => {
    let cancelled = false;
    const draw = (blob: Blob) => {
      const reader = new FileReader();
      reader.onload = () => {
        if (!cancelled) {
          setState({
            preview: typeof reader.result === "string" ? reader.result : null,
            loading: false,
          });
        }
      };
      reader.readAsDataURL(blob);
    };
    const rendered =
      (DOCUMENT_PREVIEW_TYPES.test(file.name) || PICKED_VIDEO_TYPES.test(file.name)) &&
      file.size <= PICKED_RENDER_MAX_BYTES;
    if (PICKED_PICTURE_TYPES.test(file.type) && file.size <= PICKED_PICTURE_MAX_BYTES) {
      setState({ preview: null, loading: false });
      draw(file);
    } else if (rendered) {
      // The card shows a shimmer until the render lands, so the member sees a preview is coming
      // rather than a bare name that may or may not gain a picture.
      setState({ preview: null, loading: true });
      const form = new FormData();
      form.append("file", file);
      fetch(`${BASE}/preview`, { method: "POST", body: form, credentials: "same-origin" })
        .then((res) => (res.ok ? res.blob() : null))
        .then((blob) => {
          if (cancelled) return;
          if (blob) draw(blob);
          else setState({ preview: null, loading: false });
        })
        .catch(() => {
          if (!cancelled) setState({ preview: null, loading: false });
        });
    } else {
      setState({ preview: null, loading: false });
    }
    return () => {
      cancelled = true;
    };
  }, [file]);
  return state;
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
  const { preview, loading } = usePickedPicture(file);
  return (
    <AttachmentThumbnail
      filename={file.name}
      previewUrl={preview}
      loading={loading}
      className={className}
    >
      {children}
    </AttachmentThumbnail>
  );
}
