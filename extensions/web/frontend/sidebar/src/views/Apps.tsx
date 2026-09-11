import { Button } from "@/components/ui/button";
import type { Placement } from "@/kernel/pager";
import { usePageAct } from "@/kernel/pane";
import { useMainAgent } from "@/lib/mainAgent";
import { openBuilder, openStore } from "@/lib/router";
import { useSurfaces } from "@/lib/surfaces";
import { APP_STORE_TITLE } from "@/lib/title";
import { APP_CREATOR_TITLE } from "@/lib/wizard";
import { Apps as AppsTable } from "../../../src/views/Apps";

export function Apps({
  place,
  onPlace,
}: {
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const offered = useSurfaces();
  const mainAgent = useMainAgent();
  const act = usePageAct(
    offered["app-store"] ? (
      <Button variant="send" size="bar" onClick={openStore}>
        {APP_STORE_TITLE}
      </Button>
    ) : mainAgent ? (
      <Button variant="send" size="bar" onClick={openBuilder}>
        {APP_CREATOR_TITLE}
      </Button>
    ) : null,
  );
  return (
    <>
      {act}
      <AppsTable place={place} onPlace={onPlace} />
    </>
  );
}
