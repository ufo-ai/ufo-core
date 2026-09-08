import { useEffect, useState } from "react";
import { ConversationSummary, get, slackLink, when } from "./api";
import { Loading } from "./Loading";
import { Params } from "./nav";

const SOURCE_SURFACE = "sources";
const SUBAGENT_SURFACE = "subagent";

export function Conversations(props: {
  slackTeam: string | null;
  navigate: (next: Partial<Params>) => void;
}) {
  const [conversations, setConversations] = useState<ConversationSummary[] | null>(null);
  const [showSources, setShowSources] = useState(false);
  const [showSubagents, setShowSubagents] = useState(false);

  useEffect(() => {
    get<ConversationSummary[]>("conversations").then(setConversations).catch(() => setConversations([]));
  }, []);

  if (conversations === null) return <Loading />;
  const shown = conversations.filter(
    (conversation) =>
      (showSources || conversation.surface !== SOURCE_SURFACE) &&
      (showSubagents || conversation.surface !== SUBAGENT_SURFACE),
  );
  const filters = (
    <div className="filters">
      <label>
        <input
          type="checkbox"
          checked={showSources}
          onChange={(event) => setShowSources(event.target.checked)}
        />
        sources
      </label>
      <label>
        <input
          type="checkbox"
          checked={showSubagents}
          onChange={(event) => setShowSubagents(event.target.checked)}
        />
        subagents
      </label>
    </div>
  );
  if (shown.length === 0)
    return (
      <>
        {filters}
        <div className="empty">no conversations</div>
      </>
    );
  return (
    <>
      {filters}
      <table>
        <thead>
          <tr>
            <th>surface</th>
            <th>key</th>
            <th>first message</th>
            <th>member</th>
            <th>turns</th>
            <th>last activity</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {shown.map((conversation) => (
            <tr
              key={conversation.id}
              className="row"
              onClick={() => props.navigate({ c: conversation.id, t: null })}
            >
              <td>
                <span className="chip">{conversation.surface}</span>
              </td>
              <td>
                <code>{conversation.queue_key}</code>
              </td>
              <td className="opening" title={conversation.opening_message ?? ""}>
                {conversation.opening_message ?? "—"}
              </td>
              <td>{conversation.member_email ?? "—"}</td>
              <td>{conversation.turn_count}</td>
              <td>{when(conversation.last_turn_at ?? conversation.created_at)}</td>
              <td>
                {conversation.surface === "slack" && props.slackTeam && (
                  <a
                    href={slackLink(props.slackTeam, conversation.queue_key)}
                    target="_blank"
                    rel="noopener"
                    onClick={(event) => event.stopPropagation()}
                  >
                    open in Slack ↗
                  </a>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
