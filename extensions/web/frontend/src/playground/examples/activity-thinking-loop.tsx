import { DecodeLine } from "@/components/ui/decode";
import { Marker, MarkerContent } from "@/components/ui/marker";

export function ActivityThinkingLoop() {
  return (
    <Marker>
      <MarkerContent working>
        <DecodeLine text="Comparing the two migrations" loop />
      </MarkerContent>
    </Marker>
  );
}
