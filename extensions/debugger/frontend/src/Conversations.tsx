import { useEffect, useState } from "react";
import { ConversationSummary, get, slackLink, when } from "./api";
import { Params } from "./nav";

export function Conversations(props: {
  slackTeam: string | null;
  navigate: (next: Partial<Params>) => void;
}) {
  const [conversations, setConversations] = useState<ConversationSummary[] | null>(null);

  useEffect(() => {
    get<ConversationSummary[]>("conversations").then(setConversations).catch(() => setConversations([]));
  }, []);

  if (conversations === null) return <div className="empty">loading…</div>;
  if (conversations.length === 0) return <div className="empty">no conversations</div>;
  return (
    <table>
      <thead>
        <tr>
          <th>surface</th>
          <th>key</th>
          <th>member</th>
          <th>turns</th>
          <th>last activity</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {conversations.map((conversation) => (
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
  );
}
