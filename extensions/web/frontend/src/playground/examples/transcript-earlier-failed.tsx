import { EarlierRow } from "@/components/ui/earlier-row";
import {
  MessageScroller,
  MessageScrollerContent,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "@/components/ui/message-scroller";

export function TranscriptEarlierFailed() {
  return (
    <MessageScrollerProvider>
      <MessageScroller className="w-full">
        <MessageScrollerViewport>
          <MessageScrollerContent>
            <EarlierRow
              earlier={{ pages: [], more: true, loading: false, failed: true, load: () => {} }}
            />
          </MessageScrollerContent>
        </MessageScrollerViewport>
      </MessageScroller>
    </MessageScrollerProvider>
  );
}
