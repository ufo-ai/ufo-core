import { useEffect, useState, type ComponentProps, type ReactNode } from "react";

import {
  IconFile,
  IconFileTypeCsv,
  IconFileTypeDoc,
  IconFileTypeDocx,
  IconFileTypeHtml,
  IconFileTypeJpg,
  IconFileTypePdf,
  IconFileTypePng,
  IconFileTypePpt,
  IconFileTypeSvg,
  IconFileTypeTxt,
  IconFileTypeXls,
  IconFileTypeZip,
  type Icon,
} from "@tabler/icons-react";

import { BASE } from "@/lib/api";
import { cn } from "@/lib/cn";

/** The browser's media type does not carry a file's kind — empty for `.md`, inconsistent for the
 *  office types — so the mark is read off the extension, and what the table misses is a plain sheet. */
const FILE_MARKS: Record<string, Icon> = {
  csv: IconFileTypeCsv,
  doc: IconFileTypeDoc,
  docx: IconFileTypeDocx,
  htm: IconFileTypeHtml,
  html: IconFileTypeHtml,
  jpeg: IconFileTypeJpg,
  jpg: IconFileTypeJpg,
  pdf: IconFileTypePdf,
  png: IconFileTypePng,
  ppt: IconFileTypePpt,
  pptx: IconFileTypePpt,
  svg: IconFileTypeSvg,
  txt: IconFileTypeTxt,
  xls: IconFileTypeXls,
  xlsx: IconFileTypeXls,
  zip: IconFileTypeZip,
};

export function FileMark({ filename }: { filename: string }) {
  const Mark = FILE_MARKS[filename.split(".").pop()?.toLowerCase() ?? ""] ?? IconFile;
  return <Mark aria-hidden className="size-(--size-glyph) text-ink-soft" />;
}

export function AttachmentGroup({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="attachment-group"
      className={cn(
        "-mx-sm flex min-w-0 gap-2xs overflow-x-auto overscroll-x-contain px-sm py-2xs",
        "scroll-fade-x scrollbar-none snap-x snap-mandatory scroll-px-sm",
        "*:data-[slot=attachment-thumbnail]:snap-start",
        className,
      )}
      {...props}
    />
  );
}

const DOCUMENT_PREVIEW_TYPES = /\.(pdf|docx|xlsx|pptx|csv|md|svg)$/i;

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
  const documentPreview = DOCUMENT_PREVIEW_TYPES.test(filename);
  return (
    <div
      data-slot="attachment-thumbnail"
      title={filename}
      className={cn(
        "relative flex size-(--size-thumbnail) shrink-0 items-center justify-center",
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
      {drawn === null && !shimmering ? <FileMark filename={filename} /> : null}
      {children}
    </div>
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
