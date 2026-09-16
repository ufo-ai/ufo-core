import { Bubble, BubbleContent } from "@/components/ui/bubble";
import { BubbleHeader } from "@/components/ui/bubble-header";
import { Marker, MarkerContent } from "@/components/ui/marker";
import { Message, MessageContent } from "@/components/ui/message";
import { FaceCircle } from "@/lib/memberFace";
import { FACE_PHOTO } from "@/playground/examples/face";

const SPEAKER = { name: "Priya Raman", email: "priya@work.com" };

export function TurnEntering() {
  return (
    <Message>
      <FaceCircle name={SPEAKER.name} photo={FACE_PHOTO} tint={SPEAKER.email} />
      <MessageContent>
        <BubbleHeader speaker={SPEAKER} />
        <Bubble entering>
          <BubbleContent>
            Support have read it and signed off. Send it to the workspace whenever the migration
            has a date.
          </BubbleContent>
        </Bubble>
        <Marker variant="stamp">
          <MarkerContent>09:44</MarkerContent>
        </Marker>
      </MessageContent>
    </Message>
  );
}
