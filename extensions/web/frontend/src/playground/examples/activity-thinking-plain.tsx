import { DecodeLine } from "@/components/ui/decode";
import { Marker, MarkerContent } from "@/components/ui/marker";

export function ActivityThinkingPlain() {
  return (
    <Marker>
      <MarkerContent working>
        <DecodeLine text="Reading the release branch" color={false} />
      </MarkerContent>
    </Marker>
  );
}
