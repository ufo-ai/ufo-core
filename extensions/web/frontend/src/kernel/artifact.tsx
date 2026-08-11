import { useEffect, useState } from "react";

import { Markdown } from "@/lib/markdown";
import { formatSize } from "@/lib/size";

const ARTIFACT_TEXT_BYTES = 64 * 1024;
const MARKDOWN_MEDIA_TYPE = "text/markdown";

export function isTextMedia(mediaType: string): boolean {
  return mediaType.startsWith("text/") || mediaType === "application/json";
}

/** A shared text file, read to the fold. Markdown renders as the document it is — the same
 *  renderer that draws an agent's reply — and every other text type stays preformatted, since a
 *  `.txt` or a `.csv` means the characters it holds and a markdown pass would eat them. */
export function ArtifactText({ url, mediaType }: { url: string | null; mediaType: string }) {
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
        while (!ended && read <= ARTIFACT_TEXT_BYTES) {
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
        setBody(new TextDecoder().decode(bytes.slice(0, ARTIFACT_TEXT_BYTES)));
        setBounded(read > ARTIFACT_TEXT_BYTES);
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
      {mediaType === MARKDOWN_MEDIA_TYPE ? (
        <div className="max-h-(--media-tall) overflow-y-auto rounded-panel bg-fill-subtle p-lg">
          <Markdown text={body} />
        </div>
      ) : (
        <pre className="m-0 max-h-(--media-tall) overflow-x-auto whitespace-pre-wrap rounded-panel bg-fill-subtle p-lg font-mono text-mono [overflow-wrap:anywhere]">
          {body}
        </pre>
      )}
      {bounded ? (
        <div className="font-mono text-small opacity-(--muted)">
          First {formatSize(ARTIFACT_TEXT_BYTES)} shown.
        </div>
      ) : null}
    </>
  );
}
