import { MessageHeader } from "@/components/ui/message";
import type { Speaker } from "@/lib/types";

/** The line over somebody else's words, naming who said them. Inset by the bubble's own padding, so
 *  the name starts on the same vertical as the words beneath it; the face stands beside the bubble
 *  rather than in here, and the name is the half that reaches a reader who hears the page. */
export function BubbleHeader({ speaker, className }: { speaker: Speaker; className?: string }) {
  return (
    <MessageHeader bubble className={className}>
      <span className="truncate">{speaker.name}</span>
    </MessageHeader>
  );
}
