import {
  IconFile,
  IconFileSpreadsheet,
  IconFileText,
  IconFileTypePdf,
  IconPhoto,
} from "@tabler/icons-react";
import { useEffect, useState, type ReactNode } from "react";

import { buttonVariants } from "@/components/ui/button";
import { Waiting } from "@/kernel/panel";
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

export function isTextMedia(mediaType: string): boolean {
  return mediaType.startsWith("text/") || mediaType === "application/json";
}

const SPREADSHEET_MEDIA_TYPES = new Set([
  CSV_MEDIA_TYPE,
  "application/vnd.ms-excel",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
]);

const DOCUMENT_MEDIA_PREFIXES = ["text/", "application/vnd.openxmlformats-officedocument"];
const DOCUMENT_MEDIA_TYPES = new Set(["application/json", "application/msword"]);

/** What a file with no picture of its own is drawn as: one glyph for the family its media type
 *  falls in. The families are the ones the listing narrows by — image, document, everything else —
 *  so a member who filters to Documents sees the glyph they filtered on, and a spreadsheet and a
 *  page are told apart inside that family rather than sharing one mark. A file the map does not
 *  place takes the plain sheet, which claims nothing about what is in it. */
export function MediaIcon({ mediaType }: { mediaType: string }) {
  if (mediaType.startsWith("image/")) return <IconPhoto className="size-icon" aria-hidden />;
  if (mediaType === PDF_MEDIA_TYPE) return <IconFileTypePdf className="size-icon" aria-hidden />;
  if (SPREADSHEET_MEDIA_TYPES.has(mediaType))
    return <IconFileSpreadsheet className="size-icon" aria-hidden />;
  const document =
    DOCUMENT_MEDIA_TYPES.has(mediaType) ||
    DOCUMENT_MEDIA_PREFIXES.some((prefix) => mediaType.startsWith(prefix));
  return document ? (
    <IconFileText className="size-icon" aria-hidden />
  ) : (
    <IconFile className="size-icon" aria-hidden />
  );
}

/** A file shared into a conversation, as every screen that reads one names it. The shelf carries
 *  more about it and a run's own sheet carries less; what is here is what drawing the file itself
 *  takes. */
export type SharedFile = {
  filename: string;
  subject: string | null;
  media_type: string;
  url: string | null;
  /** The picture the store rendered for a file that is not itself one — a document's first page. */
  preview_url: string | null;
};

/** The file itself, drawn once for every screen that reads one: its own picture where it has one,
 *  its characters where it is text, and a plain statement where it is neither — the download is
 *  then the whole of what a member can do with it, and a page that drew nothing there would leave
 *  them waiting on a preview that is never coming.
 *
 *  A picture whose link has expired states that, because the src is a signed URL the listing minted
 *  and the member's answer is to read the listing again. */
export function FileBody({ file }: { file: SharedFile }) {
  const [failed, setFailed] = useState(false);
  const pictured = file.preview_url ?? (file.media_type.startsWith("image/") ? file.url : null);
  if (pictured === null) {
    return isTextMedia(file.media_type) ? (
      <ArtifactText
        url={file.url}
        name={file.filename}
        mediaType={file.media_type}
        display="inline"
      />
    ) : (
      <FileNote>No preview for this file type. Download it to open it.</FileNote>
    );
  }
  if (failed) {
    return <FileNote>The image did not load. Its link may have expired — reload the page.</FileNote>;
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

/** What the file's own bytes are fetched by. It is drawn only where the file answers one: a link to
 *  nothing is a control the member presses once and learns nothing from. */
export function FileDownload({ file }: { file: SharedFile }) {
  if (!file.url) return null;
  return (
    <a
      href={file.url}
      download={file.filename}
      className={cn(buttonVariants({ variant: "send" }), "shrink-0 no-underline")}
    >
      Download
    </a>
  );
}

function FileNote({ children }: { children: ReactNode }) {
  return <div className="font-mono text-small text-ink-soft">{children}</div>;
}

/** The fetched slice as RFC 4180 rows: a quoted field holds commas and newlines, a doubled quote
 *  is a literal one. A row the byte cut ends mid-field still renders — the reader sees the table's
 *  shape, and the cap notice under it names the truncation. */
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

/** A shared text file's characters, read to the byte the fold is cut at: the read stops there and
 *  `bounded` states that the file goes on past it, so a view can say what it is showing. A view
 *  that draws the text itself — because it reads the document's own title off the first line —
 *  takes the read from here rather than fetching the file a second way. */
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

/** A shared text file, read to the fold. Markdown renders as the document it is — the same
 *  renderer that draws an agent's reply — and every other text type stays preformatted, since a
 *  `.txt` or a `.csv` means the characters it holds and a markdown pass would eat them. */
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
        <Waiting />
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

/** A csv as the table it encodes: the first row heads the columns, every later row is a record.
 *  The `typeset` register draws it as the document tables markdown renders to. */
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
