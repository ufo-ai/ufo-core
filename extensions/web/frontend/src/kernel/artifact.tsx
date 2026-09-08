import {
  IconFile,
  IconFileSpreadsheet,
  IconFileText,
  IconFileTypePdf,
  IconPhoto,
} from "@tabler/icons-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { buttonVariants } from "@/components/ui/button";
import { Sheet } from "@/components/ui/sheet";
import { Loading } from "@/kernel/panel";
import { BASE } from "@/lib/api";
import { Markdown } from "@/lib/markdown";
import { cn } from "@/lib/cn";
import { formatSize } from "@/lib/size";

export const ARTIFACT_TEXT_BYTES = 64 * 1024;
const ARTIFACT_HTML_BYTES = 256 * 1024;
const MARKDOWN_MEDIA_TYPE = "text/markdown";
const HTML_MEDIA_TYPE = "text/html";
const CSV_MEDIA_TYPE = "text/csv";
const PDF_MEDIA_TYPE = "application/pdf";
const HTML_CSP =
  "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src data:; style-src 'unsafe-inline'\">";

export const PICTURE_DID_NOT_LOAD =
  "The image did not load. Its link may have expired — reload the page.";

const TEXT_APPLICATION_MEDIA = new Set([
  "application/json",
  "application/toml",
  "application/typescript",
  "application/x-sh",
  "application/xml",
  "application/yaml",
]);

export function isTextMedia(mediaType: string): boolean {
  return mediaType.startsWith("text/") || TEXT_APPLICATION_MEDIA.has(mediaType);
}

const SPREADSHEET_MEDIA_TYPES = new Set([
  CSV_MEDIA_TYPE,
  "application/vnd.ms-excel",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
]);

const OFFICE_MEDIA_PREFIX = "application/vnd.openxmlformats-officedocument";
const WORD_MEDIA_TYPE = "application/msword";

export function MediaIcon({ mediaType }: { mediaType: string }) {
  if (mediaType.startsWith("image/")) return <IconPhoto className="size-icon" aria-hidden />;
  if (mediaType === PDF_MEDIA_TYPE) return <IconFileTypePdf className="size-icon" aria-hidden />;
  if (SPREADSHEET_MEDIA_TYPES.has(mediaType))
    return <IconFileSpreadsheet className="size-icon" aria-hidden />;
  const document =
    isTextMedia(mediaType) ||
    mediaType.startsWith(OFFICE_MEDIA_PREFIX) ||
    mediaType === WORD_MEDIA_TYPE;
  return document ? (
    <IconFileText className="size-icon" aria-hidden />
  ) : (
    <IconFile className="size-icon" aria-hidden />
  );
}

export type SharedFile = {
  filename: string;
  subject: string | null;
  media_type: string;
  size_bytes?: number;
  url: string | null;
  preview_url: string | null;
};

type FilePages = {
  pages: string[];
  pageCount: number;
  loadingMore: boolean;
};

const NO_PAGES: FilePages = { pages: [], pageCount: 0, loadingMore: false };

const PAGED_MEDIA_TYPES = new Set([
  PDF_MEDIA_TYPE,
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
]);

/** The page draws the PNGs the service returns as `data:` URLs — the page's policy admits those and no
 *  `blob:` at all — so it never draws the document's own bytes. */
async function renderPages(
  bytes: Blob,
  filename: string,
  startPage: number,
): Promise<{ pages: string[]; pageCount: number } | null> {
  const form = new FormData();
  form.append("file", bytes, filename);
  form.append("start_page", String(startPage));
  const answered = await fetch(`${BASE}/preview`, {
    method: "POST",
    body: form,
    credentials: "same-origin",
  });
  if (!answered.ok) return null;
  const body = (await answered.json()) as { pages: string[]; page_count: number };
  if (!body.pages.length) return null;
  return {
    pages: body.pages.map((page) => `data:image/png;base64,${page}`),
    pageCount: body.page_count,
  };
}

/** The route answers with a batch of its own size (`PREVIEW_PAGE_BATCH`). The render route is stateless,
 *  so every batch carries the bytes again, and they are the open file's identity. */
function useFilePages(file: SharedFile): FilePages & { loadMore: () => void } {
  const [state, setState] = useState<FilePages>(NO_PAGES);
  const held = useRef<Blob | null>(null);
  const { filename, media_type: mediaType, preview_url: previewUrl, url } = file;
  useEffect(() => {
    let cancelled = false;
    setState(NO_PAGES);
    if (url === null || previewUrl === null || !PAGED_MEDIA_TYPES.has(mediaType)) return;
    (async () => {
      const source = await fetch(url, { credentials: "same-origin" });
      if (!source.ok) return;
      const bytes = await source.blob();
      if (cancelled) return;
      held.current = bytes;
      const batch = await renderPages(bytes, filename, 1);
      if (!cancelled && batch !== null) setState({ ...NO_PAGES, ...batch });
    })().catch(() => undefined);
    return () => {
      cancelled = true;
      held.current = null;
    };
  }, [filename, mediaType, previewUrl, url]);
  const loadMore = () => {
    const bytes = held.current;
    if (bytes === null || state.loadingMore || state.pages.length >= state.pageCount) return;
    setState({ ...state, loadingMore: true });
    renderPages(bytes, filename, state.pages.length + 1)
      .then((batch) => {
        if (held.current !== bytes) return;
        setState((current) => ({
          loadingMore: false,
          pages: batch === null ? current.pages : [...current.pages, ...batch.pages],
          pageCount: batch === null ? current.pages.length : batch.pageCount,
        }));
      })
      .catch(() => {
        if (held.current !== bytes) return;
        setState((current) => ({
          ...current,
          loadingMore: false,
          pageCount: current.pages.length,
        }));
      });
  };
  return { ...state, loadMore };
}

function FileBody({ file }: { file: SharedFile }) {
  if (isTextMedia(file.media_type)) {
    return (
      <ArtifactText
        url={file.url}
        name={file.filename}
        mediaType={file.media_type}
        display="inline"
      />
    );
  }
  return <FilePicture file={file} />;
}

function FilePicture({ file }: { file: SharedFile }) {
  const [failed, setFailed] = useState(false);
  const { pages, pageCount, loadingMore, loadMore } = useFilePages(file);
  const remaining = pageCount - pages.length;
  if (pages.length > 1 || remaining > 0) {
    return (
      <div data-slot="file-pages" className="flex min-w-0 flex-col gap-lg">
        {pages.map((page, at) => (
          <img
            key={at}
            loading="lazy"
            alt={at === 0 ? file.filename : `${file.filename} page ${at + 1}`}
            src={page}
            className="w-full rounded-panel border border-edge"
          />
        ))}
        {remaining > 0 ? (
          <button
            data-slot="file-more-pages"
            type="button"
            onClick={loadMore}
            disabled={loadingMore}
            aria-label={`Load more pages of ${file.filename}`}
            className={cn(
              "w-full cursor-pointer rounded-panel border border-edge bg-card px-lg py-md",
              "font-mono text-small text-ink-soft hover:text-ink",
            )}
          >
            {loadingMore ? "Loading pages…" : `+${remaining} more`}
          </button>
        ) : null}
      </div>
    );
  }
  const pictured = file.preview_url;
  if (pictured === null) {
    return <FileNote>No preview for this file type. Download it to open it.</FileNote>;
  }
  if (failed) {
    return <FileNote>{PICTURE_DID_NOT_LOAD}</FileNote>;
  }
  return (
    <img
      alt={file.subject || file.filename}
      src={pictured}
      onError={() => setFailed(true)}
      className="max-h-(--media-tall) max-w-full object-contain"
    />
  );
}

function FileDownload({ file }: { file: SharedFile }) {
  if (!file.url) return null;
  return (
    <a
      href={file.url}
      download={file.filename}
      className={cn(buttonVariants({ variant: "send" }), "float-right ml-lg no-underline")}
    >
      Download
    </a>
  );
}

export function FileSheet({
  file,
  onClose,
  details,
}: {
  file: SharedFile;
  onClose: () => void;
  details?: ReactNode;
}) {
  const meta = [
    file.subject,
    file.media_type,
    file.size_bytes === undefined ? null : formatSize(file.size_bytes),
  ]
    .filter((part) => part)
    .join(" · ");
  return (
    <Sheet open onClose={onClose} title={file.filename}>
      <div className="flow-root">
        <FileDownload file={file} />
        <div className="font-mono text-small text-ink-soft">{meta}</div>
        {details}
      </div>
      <FileBody file={file} />
    </Sheet>
  );
}

function FileNote({ children }: { children: ReactNode }) {
  return <div className="font-mono text-small text-ink-soft">{children}</div>;
}

/** RFC 4180 rows: a quoted field holds commas and newlines, and a doubled quote is a literal one. */
function csvRows(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  let at = 0;
  const settle = () => {
    row.push(field);
    field = "";
  };
  const land = () => {
    settle();
    rows.push(row);
    row = [];
  };
  while (at < text.length) {
    const char = text[at];
    if (quoted) {
      if (char === '"' && text[at + 1] === '"') {
        field += '"';
        at += 2;
        continue;
      }
      if (char === '"') quoted = false;
      else field += char;
    } else if (char === '"' && field === "") quoted = true;
    else if (char === ",") settle();
    else if (char === "\n") land();
    else if (char !== "\r") field += char;
    at += 1;
  }
  if (field !== "" || row.length) land();
  return rows;
}

export function useTextArtifact(
  url: string | null,
  byteLimit: number = ARTIFACT_TEXT_BYTES,
): { body: string | null; bounded: boolean; message: string | null } {
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
        while (!ended && read <= byteLimit) {
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
        setBody(new TextDecoder().decode(bytes.slice(0, byteLimit)));
        setBounded(read > byteLimit);
      } catch {
        if (live) setMessage("Network error — try again.");
      }
    })();
    return () => {
      live = false;
    };
  }, [byteLimit, url]);

  return { body, bounded, message };
}

export function ArtifactText({
  url,
  name,
  mediaType,
  display = "frame",
}: {
  url: string | null;
  name: string;
  mediaType: string;
  display?: "excerpt" | "frame" | "inline";
}) {
  const byteLimit = mediaType === HTML_MEDIA_TYPE ? ARTIFACT_HTML_BYTES : ARTIFACT_TEXT_BYTES;
  const { body, bounded, message } = useTextArtifact(url, byteLimit);

  if (message) return <div className="font-mono text-small text-ink-soft">{message}</div>;
  if (body === null)
    return (
      <div>
        <Loading />
      </div>
    );
  if (mediaType === HTML_MEDIA_TYPE && bounded)
    return (
      <div className="font-mono text-small text-ink-soft">
        This page is larger than {formatSize(byteLimit)}. Download it to open it.
      </div>
    );
  return (
    <>
      {mediaType === MARKDOWN_MEDIA_TYPE || mediaType === CSV_MEDIA_TYPE ? (
        <div
          data-artifact-document
          className={cn(
            "relative rounded-panel bg-fill p-lg",
            display === "excerpt" && "max-h-24 overflow-hidden",
            display === "frame" && "max-h-(--media-tall) overflow-y-auto",
          )}
        >
          {mediaType === MARKDOWN_MEDIA_TYPE ? <Markdown text={body} /> : <CsvTable text={body} />}
          {display === "excerpt" ? (
            <div
              aria-hidden
              className="pointer-events-none absolute inset-x-0 bottom-0 h-2xl bg-linear-to-t from-fill"
            />
          ) : null}
        </div>
      ) : mediaType === HTML_MEDIA_TYPE ? (
        <iframe
          sandbox=""
          srcDoc={HTML_CSP + body}
          referrerPolicy="no-referrer"
          title={name}
          className="h-(--media-tall) max-h-(--media-tall) w-full rounded-panel border border-edge"
        />
      ) : (
        <pre className="m-0 max-h-(--media-tall) overflow-x-auto whitespace-pre-wrap rounded-panel bg-fill p-lg font-mono text-mono [overflow-wrap:anywhere]">
          {body}
        </pre>
      )}
      {bounded && display !== "excerpt" ? (
        <div className="font-mono text-small text-ink-soft">
          First {formatSize(byteLimit)} shown.
        </div>
      ) : null}
    </>
  );
}

function CsvTable({ text }: { text: string }) {
  const [head, ...records] = csvRows(text);
  return (
    <div className="typeset">
      <table>
        <thead>
          <tr>
            {(head ?? []).map((cell, column) => (
              <th key={column}>{cell}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {records.map((cells, row) => (
            <tr key={row}>
              {cells.map((cell, column) => (
                <td key={column}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
