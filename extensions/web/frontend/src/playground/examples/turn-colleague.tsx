import { Bubble, BubbleContent } from "@/components/ui/bubble";
import { BubbleHeader } from "@/components/ui/bubble-header";
import { Marker, MarkerContent } from "@/components/ui/marker";
import { Message, MessageContent } from "@/components/ui/message";
import { FaceCircle } from "@/lib/memberFace";
import { FACE_PHOTO } from "@/playground/examples/face";

const SPEAKER = { name: "Priya Raman", email: "priya@work.com" };

export function TurnColleague() {
  return (
    <Message>
      <FaceCircle name={SPEAKER.name} photo={FACE_PHOTO} tint={SPEAKER.email} />
      <MessageContent>
        <BubbleHeader speaker={SPEAKER} />
        <Bubble>
          <BubbleContent>
            Draft the release note for 2.14. It has to name the topic preferences screen and the
            sign-in link that now lasts fifteen minutes, and support read it before the workspace
            does.
          </BubbleContent>
        </Bubble>
        <Marker variant="stamp">
          <MarkerContent>09:38</MarkerContent>
        </Marker>
      </MessageContent>
    </Message>
  );
}
