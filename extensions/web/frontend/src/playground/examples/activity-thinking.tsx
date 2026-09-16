import { DecodeLine } from "@/components/ui/decode";
import { Marker, MarkerContent } from "@/components/ui/marker";

export function ActivityThinking() {
  return (
    <Marker>
      <MarkerContent working>
        <DecodeLine text="Reading the release branch" />
      </MarkerContent>
    </Marker>
  );
}
