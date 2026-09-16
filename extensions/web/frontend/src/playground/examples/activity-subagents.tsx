import { Marker, MarkerContent } from "@/components/ui/marker";

export function ActivitySubagents() {
  return (
    <div className="flex max-w-(--container-answer) flex-col gap-2xs">
      <Marker>
        <MarkerContent working>Reading the release branch</MarkerContent>
      </Marker>
      <Marker indent>
        <MarkerContent working truncate>
          Reading src/components/ui/message-scroller.tsx, src/kernel/messages.tsx, and every file
          CHANGELOG.md has named since Friday
        </MarkerContent>
      </Marker>
      <Marker indent>
        <MarkerContent working truncate>
          Running the 41 tests behind the topic preferences screen
        </MarkerContent>
      </Marker>
    </div>
  );
}
