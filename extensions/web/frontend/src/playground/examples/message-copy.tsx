import { Bubble, BubbleContent } from "@/components/ui/bubble";
import { CopyAct } from "@/components/ui/copy-act";
import { Marker, MarkerContent } from "@/components/ui/marker";
import { Message, MessageContent } from "@/components/ui/message";

const REPLY =
  "The note is a draft on the release conversation, and support have the link. Ask for a second " +
  "pass once the billing migration has a date.";

export function MessageCopy() {
  return (
    <Message>
      <MessageContent>
        <Bubble variant="ghost">
          <BubbleContent>{REPLY}</BubbleContent>
        </Bubble>
        <Marker variant="stamp" className="mt-2xs gap-md">
          <CopyAct text={REPLY} />
          <MarkerContent>Opus 5 · 1,284 tokens · $0.004</MarkerContent>
        </Marker>
      </MessageContent>
    </Message>
  );
}
