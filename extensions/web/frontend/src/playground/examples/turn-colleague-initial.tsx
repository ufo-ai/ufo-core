import { Bubble, BubbleContent } from "@/components/ui/bubble";
import { BubbleHeader } from "@/components/ui/bubble-header";
import { Marker, MarkerContent } from "@/components/ui/marker";
import { Message, MessageContent } from "@/components/ui/message";
import { FaceCircle } from "@/lib/memberFace";

const SPEAKER = { name: "Tom Okafor", email: "tom@work.com" };

export function TurnColleagueInitial() {
  return (
    <Message>
      <FaceCircle name={SPEAKER.name} photo={null} tint={SPEAKER.email} />
      <MessageContent>
        <BubbleHeader speaker={SPEAKER} />
        <Bubble>
          <BubbleContent>
            Billing signed off on the migration this morning, so the note can name a date. I put
            their reply on the release conversation.
          </BubbleContent>
        </Bubble>
        <Marker variant="stamp">
          <MarkerContent>09:52</MarkerContent>
        </Marker>
      </MessageContent>
    </Message>
  );
}
