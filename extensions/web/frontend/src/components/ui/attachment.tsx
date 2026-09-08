import { useEffect, useState, type ComponentProps, type ReactNode } from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { BASE } from "@/lib/api";
import { cn } from "@/lib/cn";

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

export function AttachmentGroup({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="attachment-group"
      className={cn(
        "-mx-sm flex min-w-0 gap-2xs overflow-x-auto overscroll-x-contain px-sm py-2xs",
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

/** The browser's media type does not carry the kind — empty for `.md`, inconsistent for office types —
 *  so the badge is read off the extension. */
export function attachmentBadgeFor(filename: string): string | null {
  const dot = filename.lastIndexOf(".");
  const ext = dot === -1 ? "" : filename.slice(dot + 1).toLowerCase();
  return ATTACHMENT_BADGES[ext] ?? null;
}

export function AttachmentThumbnail({
  filename,
  previewUrl,
  loading = false,
  className,
  children,
}: {
  filename: string;
  previewUrl: string | null;
  loading?: boolean;
  className?: string;
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
        "overflow-hidden rounded-bubble border border-edge bg-card text-card-foreground",
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
// The route refuses a body over `MAX_REQUEST_BYTES` (25 MB, `preview` in ufo_ext_web/surface.py), so a
// file admitted over that is uploaded whole only to earn a 413.
const PICKED_RENDER_MAX_BYTES = 25 * 1024 * 1024;
const PICKED_VIDEO_TYPES = /\.(mp4|mov|webm|mkv)$/i;

/** The page's policy admits `data:` URLs and no `blob:` at all, so a document is sent to the preview
 *  service and the card draws the raster it returns — even an SVG, which the page never draws itself. */
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
      setState({ preview: null, loading: true });
      const form = new FormData();
      form.append("file", file);
      form.append("pages", "1");
      fetch(`${BASE}/preview`, { method: "POST", body: form, credentials: "same-origin" })
        .then((res) => (res.ok ? (res.json() as Promise<{ pages: string[] }>) : null))
        .then((body) => {
          if (cancelled) return;
          const cover = body?.pages[0];
          setState({
            preview: cover === undefined ? null : `data:image/png;base64,${cover}`,
            loading: false,
          });
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
