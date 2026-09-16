import { Bubble, BubbleContent } from "@/components/ui/bubble";
import { Marker, MarkerContent } from "@/components/ui/marker";
import { Message, MessageContent } from "@/components/ui/message";

export function TurnStamp() {
  return (
    <Message>
      <MessageContent>
        <Bubble variant="ghost">
          <BubbleContent>
            The draft is on the release conversation, and support have the link. Ask for a second
            pass once the billing migration has a date.
          </BubbleContent>
        </Bubble>
        <Marker variant="stamp" reveal>
          <MarkerContent>Opus 5 · 1,284 tokens · $0.004</MarkerContent>
        </Marker>
      </MessageContent>
    </Message>
  );
}
