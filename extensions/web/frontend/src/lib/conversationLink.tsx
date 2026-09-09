import { useEffect } from "react";

import { chatHash } from "@/lib/route";
import { seekChat, useRail } from "@/lib/railStore";

const CONVERSATION_WORD = "Conversation";

/** A link to one conversation, by what the conversation is called. An app page runs its own bundle
 *  and never reads the rail, so a title the rail does not hold is sought here rather than waited
 *  for; until an answer lands the anchor reads as the plain word, never as the id. */
export function ConversationLink({ id }: { id: string }) {
  const rail = useRail();
  const title =
    rail.rows.find((row) => row.conversation_id === id)?.title || rail.linked[id]?.description;
  useEffect(() => {
    if (!title) seekChat(id);
  }, [id, title]);
  return (
    <a
      href={chatHash(id)}
      className="text-inherit underline-offset-2 hover:underline focus-visible:underline"
    >
      {title || CONVERSATION_WORD}
    </a>
  );
}
