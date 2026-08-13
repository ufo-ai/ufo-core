import { useEffect, useState } from "react";

import { Search } from "@/components/ui/field";
import type { Placement } from "@/kernel/pager";
import { COLUMN, PageHeader, Pane } from "@/kernel/pane";
import { usePlaceRecorder } from "@/kernel/place";
import type { PlaceStep, WorkspacePlace } from "@/lib/route";
import { TabPanel, TabStrip } from "@/kernel/tabs";
import { cn } from "@/lib/cn";
import type { PaneView } from "@/views/registry";

/** A destination the sidebar reaches: a title, a tab array, and one registry. Workspace, Customize
 *  and each top-level section differ in nothing else, so they are one shell — a second copy would
 *  be a second answer to what opening a row does to history. A destination holding one view draws
 *  no strip, because the pane is then the page and a strip of one names what the `<h1>` above it
 *  already said. The header carries the search over the whole destination; the filter that narrows
 *  the records to a family stays with them. */
export function TabbedPane<Tab extends string>({
  title,
  group,
  tabs,
  views,
  view,
  place,
  onPlace,
}: {
  title: string;
  group: string;
  tabs: readonly Tab[];
  views: Record<Tab, PaneView>;
  view: Tab;
  place: WorkspacePlace;
  onPlace: (view: Tab, place: WorkspacePlace, step: PlaceStep) => void;
}) {
  const { key, merged, record } = usePlaceRecorder({
    view,
    place,
    remountOnPlace: views[view].remountOnPlace,
    onPlace: (next, step) => onPlace(view, next, step),
  });
  const search = views[view].search;
  const headed = views[view].ownsHeader === true;
  return (
    <Pane>
      {headed ? null : (
        <PageHeader
          aside={
            search ? (
              <PaneSearch
                placeholder={search}
                query={place.q ?? ""}
                onSearch={(q) => record({ q: q || undefined, after: undefined })}
              />
            ) : null
          }
        >
          <h1 className="m-0 text-title font-strong">{title}</h1>
        </PageHeader>
      )}
      {tabs.length > 1 ? (
        <>
          <TabStrip
            group={group}
            tabs={tabs}
            current={view}
            label={(name) => views[name].label}
            onPick={(name) => onPlace(name, {}, "push")}
          />
          <TabPanel
            group={group}
            current={view}
            className={cn(
              COLUMN,
              "flex flex-1 flex-col overflow-y-auto scrollbar-gutter-stable p-2xl",
              headed && "pt-0",
            )}
            data-testid={group}
          >
            <Registered key={key} view={view} views={views} place={merged} onPlace={record} />
          </TabPanel>
        </>
      ) : (
        <div
          className={cn(
            COLUMN,
            "flex flex-1 flex-col overflow-y-auto scrollbar-gutter-stable p-2xl",
            headed && "pt-0",
          )}
          data-testid={group}
        >
          <Registered key={key} view={view} views={views} place={merged} onPlace={record} />
        </div>
      )}
    </Pane>
  );
}

/** What the member has typed is theirs until they submit it, so the box holds its own text and
 *  takes the place's word for it only when the place itself names a different query — a filter
 *  picked beside a half-written search leaves that search standing. */
function PaneSearch({
  placeholder,
  query,
  onSearch,
}: {
  placeholder: string;
  query: string;
  onSearch: (query: string) => void;
}) {
  const [typed, setTyped] = useState(query);
  useEffect(() => setTyped(query), [query]);
  return (
    <Search
      label="Search"
      placeholder={placeholder}
      value={typed}
      onChange={(event) => setTyped(event.target.value)}
      onSubmit={() => onSearch(typed.trim())}
      className="w-(--container-control-row)"
    />
  );
}

function Registered<Tab extends string>({
  view,
  views,
  place,
  onPlace,
}: {
  view: Tab;
  views: Record<Tab, PaneView>;
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  return views[view].render(place, onPlace);
}
