import { useEffect, useState } from "react";
import { apiUrl, get, when, WorkspaceFile } from "./api";

export function Files(props: { conversationId: string }) {
  const [files, setFiles] = useState<WorkspaceFile[] | null>(null);

  useEffect(() => {
    setFiles(null);
    get<WorkspaceFile[]>(`conversations/${props.conversationId}/files`)
      .then(setFiles)
      .catch(() => setFiles([]));
  }, [props.conversationId]);

  if (files === null) return <div className="empty">loading…</div>;
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
          </tr>
        </thead>
        <tbody>
          {files.map((file) => (
            <tr key={file.path}>
              <td>
                <a
                  href={apiUrl(`conversations/${props.conversationId}/files/${file.path}`)}
                  download={file.path.split("/").pop()}
                >
                  <code>{file.path}</code>
                </a>
              </td>
              <td>{size(file.size_bytes)}</td>
              <td>{when(file.modified_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
