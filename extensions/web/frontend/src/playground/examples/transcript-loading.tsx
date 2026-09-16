import {
  MessageScroller,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "@/components/ui/message-scroller";
import { Skeleton } from "@/components/ui/skeleton";

export function TranscriptLoading() {
  return (
    <MessageScrollerProvider>
      <MessageScroller className="h-(--container-connect) w-full">
        <MessageScrollerViewport>
          <MessageScrollerContent>
            <MessageScrollerItem messageId="asked">
              <Skeleton className="h-(--size-glyph) w-1/3" />
            </MessageScrollerItem>
            <MessageScrollerItem messageId="answering">
              <div className="flex flex-col gap-sm">
                <Skeleton className="h-(--size-glyph) w-full" />
                <Skeleton className="h-(--size-glyph) w-full" />
                <Skeleton className="h-(--size-glyph) w-1/2" />
              </div>
            </MessageScrollerItem>
          </MessageScrollerContent>
        </MessageScrollerViewport>
      </MessageScroller>
    </MessageScrollerProvider>
  );
}
