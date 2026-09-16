import { Bubble, BubbleContent } from "@/components/ui/bubble";
import { Marker, MarkerContent } from "@/components/ui/marker";
import { Message, MessageContent } from "@/components/ui/message";

export function TurnMine() {
  return (
    <Message align="end">
      <MessageContent>
        <Bubble variant="said" align="end">
          <BubbleContent>
            {"Send the draft to support first.\nI will read the final copy at 16:00."}
          </BubbleContent>
        </Bubble>
        <Marker variant="stamp" align="end">
          <MarkerContent>09:41</MarkerContent>
        </Marker>
      </MessageContent>
    </Message>
  );
}
