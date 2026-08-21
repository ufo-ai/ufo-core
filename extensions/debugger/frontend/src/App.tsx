import { useEffect, useState } from "react";
import { get, WorkspaceMeta } from "./api";
import { Conversations } from "./Conversations";
import { Fleet } from "./Fleet";
import { useParams } from "./nav";
import { Session } from "./Session";

export function App() {
  const [params, navigate] = useParams();
  const [meta, setMeta] = useState<WorkspaceMeta | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picker, setPicker] = useState(params.ws ?? "");

  const fleet = !params.ws && !params.c;

  useEffect(() => setPicker(params.ws ?? ""), [params.ws]);

  useEffect(() => {
    document.title = (params.c ?? (fleet ? "Fleet" : "Conversations")) + " · ufo debugger";
  }, [params.c, fleet]);

  useEffect(() => {
    setMeta(null);
    setError(null);
    if (fleet) return;
    get<WorkspaceMeta>("workspace")
      .then(setMeta)
      .catch((err: Error) => setError(err.message));
  }, [params.ws, fleet]);

  return (
    <>
      <header>
        <h1 onClick={() => navigate({ ws: null, c: null, t: null })}>ufo · session debugger</h1>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            navigate({ ws: picker.trim() || null, c: null, t: null });
          }}
        >
          <input
            value={picker}
            onChange={(event) => setPicker(event.target.value)}
            placeholder="workspace domain or UUID"
          />
          <button type="submit">open</button>
        </form>
        {meta && (
          <span className="meta crumb" onClick={() => navigate({ c: null, t: null })}>
            workspace <code>{meta.workspace_id}</code>
            {meta.slack_team && (
              <>
                {" · slack "}
                <code>{meta.slack_team}</code>
              </>
            )}
          </span>
        )}
        <a
          className="cross-link"
          href={`/surface/memory${params.ws ? `?ws=${encodeURIComponent(params.ws)}` : ""}`}
        >
          memory explorer →
        </a>
      </header>
      <main>
        {error ? (
          <div className="empty">
            {error} — check the workspace target and that your session is signed in.
          </div>
        ) : fleet ? (
          <Fleet navigate={navigate} />
        ) : params.c ? (
          <Session conversationId={params.c} selectedTurn={params.t} navigate={navigate} />
        ) : (
          <Conversations
            key={params.ws ?? "own"}
            slackTeam={meta?.slack_team ?? null}
            navigate={navigate}
          />
        )}
      </main>
    </>
  );
}
