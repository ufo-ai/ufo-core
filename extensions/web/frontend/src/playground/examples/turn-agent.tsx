import { Bubble, BubbleContent } from "@/components/ui/bubble";
import { Marker, MarkerContent } from "@/components/ui/marker";
import { Message, MessageContent } from "@/components/ui/message";

export function TurnAgent() {
  return (
    <Message>
      <MessageContent>
        <Bubble variant="ghost">
          <BubbleContent>
            The draft is on the release conversation. It runs to three paragraphs: what 2.14
            changes, the two screens a member will notice, and what support should say to anyone
            who asks why the old sign-in link stopped working. Nothing in it names a date, because
            the rollout still waits on the billing migration.
          </BubbleContent>
        </Bubble>
        <Marker variant="stamp">
          <MarkerContent>Opus 5 · 1,284 tokens · $0.004</MarkerContent>
        </Marker>
      </MessageContent>
    </Message>
  );
}
