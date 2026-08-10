import type { Placement } from "@/kernel/pager";
import { usePlaceRecorder } from "@/kernel/place";
import type { PlaceStep, Section, WorkspacePlace } from "@/lib/route";
import { SECTION_VIEWS } from "@/views/registry";

/** A section the sidebar reaches directly: the pane is the page, so it heads itself and holds no
 *  tab strip. The place bookkeeping is the workspace's, so a row opened here answers Back the same
 *  way it does under a tab. */
export function SectionPane({
  section,
  place,
  onPlace,
}: {
  section: Section;
  place: WorkspacePlace;
  onPlace: (section: Section, place: WorkspacePlace, step: PlaceStep) => void;
}) {
  const { key, merged, record } = usePlaceRecorder({
    view: section,
    place,
    remountOnPlace: SECTION_VIEWS[section].remountOnPlace,
    onPlace: (next, step) => onPlace(section, next, step),
  });

  return (
    <main className="flex min-h-0 min-w-0 flex-col">
      <div className="flex items-baseline gap-md px-2xl pt-lg">
        <h1 className="m-0 text-title font-strong">{SECTION_VIEWS[section].label}</h1>
      </div>
      <div
        className="flex flex-1 flex-col overflow-y-auto p-2xl"
        data-testid="section"
      >
        <Rendered key={key} section={section} place={merged} onPlace={record} />
      </div>
    </main>
  );
}

function Rendered({
  section,
  place,
  onPlace,
}: {
  section: Section;
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  return SECTION_VIEWS[section].render(place, onPlace);
}
