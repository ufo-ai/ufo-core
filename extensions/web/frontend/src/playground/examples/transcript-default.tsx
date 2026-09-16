import { Bubble, BubbleContent } from "@/components/ui/bubble";
import { Message, MessageContent } from "@/components/ui/message";
import {
  MessageScroller,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "@/components/ui/message-scroller";
import { FaceCircle } from "@/lib/memberFace";
import { FACE_PHOTO } from "@/playground/examples/face";

const SPEAKER = { name: "Priya Raman", email: "priya@work.com" };

const TURNS = [
  { id: "asked", mine: false, says: "Where did the 2.14 release note end up?" },
  { id: "found", mine: true, says: "It is a draft on the release conversation." },
  { id: "when", mine: false, says: "Send it to support before 16:00." },
  { id: "sent", mine: true, says: "Sent. Support have it now." },
];

export function TranscriptDefault() {
  return (
    <MessageScrollerProvider>
      <MessageScroller className="h-(--container-connect) w-full">
        <MessageScrollerViewport>
          <MessageScrollerContent>
            {TURNS.map(({ id, mine, says }) => (
              <MessageScrollerItem key={id} messageId={id}>
                <Message align={mine ? "end" : "start"}>
                  {mine ? null : (
                    <FaceCircle name={SPEAKER.name} photo={FACE_PHOTO} tint={SPEAKER.email} />
                  )}
                  <MessageContent>
                    <Bubble variant={mine ? "said" : "default"} align={mine ? "end" : "start"}>
                      <BubbleContent>{says}</BubbleContent>
                    </Bubble>
                  </MessageContent>
                </Message>
              </MessageScrollerItem>
            ))}
          </MessageScrollerContent>
        </MessageScrollerViewport>
      </MessageScroller>
    </MessageScrollerProvider>
  );
}
