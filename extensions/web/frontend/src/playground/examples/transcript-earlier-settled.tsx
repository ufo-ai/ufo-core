import { EarlierRow } from "@/components/ui/earlier-row";
import {
  MessageScroller,
  MessageScrollerContent,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "@/components/ui/message-scroller";

export function TranscriptEarlierSettled() {
  return (
    <MessageScrollerProvider>
      <MessageScroller className="w-full">
        <MessageScrollerViewport>
          <MessageScrollerContent>
            <EarlierRow
              earlier={{ pages: [], more: false, loading: false, failed: false, load: () => {} }}
            />
          </MessageScrollerContent>
        </MessageScrollerViewport>
      </MessageScroller>
    </MessageScrollerProvider>
  );
}
