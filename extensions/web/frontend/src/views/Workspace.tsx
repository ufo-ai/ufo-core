import type { Placement } from "@/kernel/pager";
import { usePlaceRecorder } from "@/kernel/place";
import type { PlaceStep } from "@/lib/route";
import { WORKSPACE_TABS, type WorkspacePlace, type WorkspaceTab } from "@/lib/route";
import { TabPanel, TabStrip } from "@/kernel/tabs";
import { WORKSPACE_VIEWS } from "@/views/registry";

export function Workspace({
  view,
  place,
  onPlace,
}: {
  view: WorkspaceTab;
  place: WorkspacePlace;
  onPlace: (view: WorkspaceTab, place: WorkspacePlace, step: PlaceStep) => void;
}) {
  const { key, merged, record } = usePlaceRecorder({
    view,
    place,
    remountOnPlace: WORKSPACE_VIEWS[view].remountOnPlace,
    onPlace: (next, step) => onPlace(view, next, step),
  });

  return (
    <main className="flex min-h-0 min-w-0 flex-col">
      <div className="flex items-baseline gap-md px-2xl pt-lg">
        <h1 className="m-0 text-title font-strong">Workspace</h1>
      </div>
      <TabStrip
        group="workspace"
        tabs={WORKSPACE_TABS}
        current={view}
        label={(name) => WORKSPACE_VIEWS[name].label}
        onPick={(name) => onPlace(name, {}, "push")}
      />
      <TabPanel
        group="workspace"
        current={view}
        className="flex flex-1 flex-col overflow-y-auto p-2xl"
        data-testid="workspace"
      >
        <Registered key={key} view={view} place={merged} onPlace={record} />
      </TabPanel>
    </main>
  );
}

function Registered({
  view,
  place,
  onPlace,
}: {
  view: WorkspaceTab;
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  return WORKSPACE_VIEWS[view].render(place, onPlace);
}
