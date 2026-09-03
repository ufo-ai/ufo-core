import { useEffect, useState } from "react";
import { apiUrl, get, when, WorkspaceFile } from "./api";
import { Loading } from "./Loading";

const IMAGE_MIME: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
  svg: "image/svg+xml",
  bmp: "image/bmp",
  ico: "image/x-icon",
};
const MAX_INLINE_TEXT = 200_000;

type View =
  | { kind: "loading" }
  | { kind: "text"; text: string; truncated: boolean }
  | { kind: "image"; url: string }
  | { kind: "binary" }
  | { kind: "error"; message: string };

export function Files(props: { conversationId: string }) {
  const [files, setFiles] = useState<WorkspaceFile[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    setFiles(null);
    setOpen(null);
    get<WorkspaceFile[]>(`conversations/${props.conversationId}/files`)
      .then(setFiles)
      .catch(() => setFiles([]));
  }, [props.conversationId]);

  if (files === null) return <Loading />;
  if (files.length === 0) return <div className="empty">no workspace files</div>;
  return (
    <section className="panel">
      <h2>workspace files · the sandbox's working tree</h2>
      <table>
        <thead>
          <tr>
            <th>path</th>
            <th>size</th>
            <th>modified</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {files.map((file) => (
            <tr key={file.path} className={open === file.path ? "row selected" : "row"}>
              <td onClick={() => setOpen(open === file.path ? null : file.path)}>
                <code>{file.path}</code>
              </td>
              <td>{size(file.size_bytes)}</td>
              <td>{when(file.modified_at)}</td>
              <td>
                <a
                  href={apiUrl(`conversations/${props.conversationId}/files/${file.path}`)}
                  download={file.path.split("/").pop()}
                  onClick={(event) => event.stopPropagation()}
                >
                  download ↓
                </a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {open !== null && (
        <Viewer conversationId={props.conversationId} path={open} />
      )}
    </section>
  );
}

function Viewer(props: { conversationId: string; path: string }) {
  const [view, setView] = useState<View>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    setView({ kind: "loading" });
    const extension = props.path.split(".").pop()?.toLowerCase() ?? "";
    fetch(apiUrl(`conversations/${props.conversationId}/files/${props.path}`), {
      credentials: "same-origin",
    })
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const buffer = await response.arrayBuffer();
        if (cancelled) return;
        const imageType = IMAGE_MIME[extension];
        if (imageType) {
          objectUrl = URL.createObjectURL(new Blob([buffer], { type: imageType }));
          if (cancelled) {
            URL.revokeObjectURL(objectUrl);
            return;
          }
          setView({ kind: "image", url: objectUrl });
          return;
        }
        const text = asText(buffer);
        if (text === null) {
          setView({ kind: "binary" });
          return;
        }
        setView({
          kind: "text",
          text: text.slice(0, MAX_INLINE_TEXT),
          truncated: text.length > MAX_INLINE_TEXT,
        });
      })
      .catch((error: Error) => {
        if (!cancelled) setView({ kind: "error", message: error.message });
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [props.conversationId, props.path]);

  return (
    <div className="file-view">
      <div className="meta">
        <code>{props.path}</code>
      </div>
      {view.kind === "loading" && <Loading />}
      {view.kind === "error" && <div className="empty error-text">{view.message}</div>}
      {view.kind === "binary" && (
        <div className="empty">binary file — use the download link</div>
      )}
      {view.kind === "image" && <img className="file-image" src={view.url} alt={props.path} />}
      {view.kind === "text" && (
        <>
          {view.truncated && (
            <div className="meta">showing the first {MAX_INLINE_TEXT.toLocaleString()} characters</div>
          )}
          <pre>{view.text}</pre>
        </>
      )}
    </div>
  );
}

function asText(buffer: ArrayBuffer): string | null {
  const bytes = new Uint8Array(buffer);
  if (bytes.includes(0)) return null;
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(buffer);
  } catch {
    return null;
  }
}

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
