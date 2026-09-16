import { Marker, MarkerContent } from "@/components/ui/marker";

export function ActivityWorking() {
  return (
    <div className="max-w-(--container-answer)">
      <Marker>
        <MarkerContent working>Reading the release branch</MarkerContent>
      </Marker>
    </div>
  );
}
