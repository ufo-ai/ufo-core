import { useEffect, useState } from "react";
import { FleetListing, get, when } from "./api";
import { Params } from "./nav";

export function Fleet(props: { navigate: (next: Partial<Params>) => void }) {
  const [listing, setListing] = useState<FleetListing | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    get<FleetListing>("fleet")
      .then(setListing)
      .catch((err: Error) => setError(err.message));
  }, []);

  if (error) return <div className="empty">{error}</div>;
  if (listing === null) return <div className="empty">loading…</div>;

  const open = (workspaceId: string, domain: string | null, conversation: string | null) =>
    props.navigate({ ws: domain ?? workspaceId, c: conversation, t: null });

  return (
    <>
      <h2>workspaces</h2>
      <table>
        <thead>
          <tr>
            <th>domain</th>
            <th>workspace</th>
            <th>members</th>
            <th>conversations</th>
            <th>last activity</th>
          </tr>
        </thead>
        <tbody>
          {listing.workspaces.map((workspace) => (
            <tr
              key={workspace.workspace_id}
              className="row"
              onClick={() => open(workspace.workspace_id, workspace.domain, null)}
            >
              <td>{workspace.domain ?? "—"}</td>
              <td>
                <code>{workspace.workspace_id}</code>
              </td>
              <td>{workspace.members}</td>
              <td>{workspace.conversations}</td>
              <td>{when(workspace.last_turn_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h2>recent threads</h2>
      {listing.threads.length === 0 ? (
        <div className="empty">no threads</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>domain</th>
              <th>surface</th>
              <th>thread</th>
              <th>turns</th>
              <th>last activity</th>
            </tr>
          </thead>
          <tbody>
            {listing.threads.map((thread) => (
              <tr
                key={thread.conversation_id}
                className="row"
                onClick={() => open(thread.workspace_id, thread.domain, thread.conversation_id)}
              >
                <td>{thread.domain ?? thread.workspace_id}</td>
                <td>
                  <span className="chip">{thread.surface}</span>
                </td>
                <td>{thread.title ?? <code>{thread.queue_key}</code>}</td>
                <td>{thread.turn_count}</td>
                <td>{when(thread.last_turn_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
