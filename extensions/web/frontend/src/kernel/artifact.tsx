import { useEffect, useState } from "react";

import { Markdown } from "@/lib/markdown";
import { cn } from "@/lib/cn";
import { formatSize } from "@/lib/size";

const ARTIFACT_TEXT_BYTES = 64 * 1024;
const ARTIFACT_HTML_BYTES = 256 * 1024;
const MARKDOWN_MEDIA_TYPE = "text/markdown";
const HTML_MEDIA_TYPE = "text/html";
const CSV_MEDIA_TYPE = "text/csv";
const HTML_CSP =
  "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src data:; style-src 'unsafe-inline'\">";

export function isTextMedia(mediaType: string): boolean {
  return mediaType.startsWith("text/") || mediaType === "application/json";
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
  const [body, setBody] = useState<string | null>(null);
  const [bounded, setBounded] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const byteLimit = mediaType === HTML_MEDIA_TYPE ? ARTIFACT_HTML_BYTES : ARTIFACT_TEXT_BYTES;

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

  if (message) return <div className="font-mono text-small text-ink-soft">{message}</div>;
  if (body === null) return <div>Loading…</div>;
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
