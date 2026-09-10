import { useEffect } from "react";

import { chatHash } from "@/lib/route";
import { seekChat, useRail } from "@/lib/railStore";

const CONVERSATION_WORD = "Conversation";

/** What one conversation is called. An app page runs its own bundle and never reads the rail, so a
 *  title the rail does not hold is sought here rather than waited for; until an answer lands there
 *  is no title, and a screen says the word it stands for rather than the id. */
export function useConversationTitle(id: string): string | null {
  const rail = useRail();
  const title =
    rail.rows.find((row) => row.conversation_id === id)?.title || rail.linked[id]?.description;
  useEffect(() => {
    if (!title) seekChat(id);
  }, [id, title]);
  return title || null;
}

/** A link to one conversation, by what the conversation is called. */
export function ConversationLink({ id }: { id: string }) {
  const title = useConversationTitle(id);
  return (
    <a
      href={chatHash(id)}
      className="text-inherit underline-offset-2 hover:underline focus-visible:underline"
    >
      {title || CONVERSATION_WORD}
    </a>
  );
}
