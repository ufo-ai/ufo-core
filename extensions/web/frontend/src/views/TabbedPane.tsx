import { useEffect, useState } from "react";

import { Search } from "@/components/ui/field";
import type { Placement } from "@/kernel/pager";
import { BANDS, Page, PageActs, PageHeader, PageToolbar, Pane } from "@/kernel/pane";
import { usePlaceRecorder } from "@/kernel/place";
import type { PlaceStep, WorkspacePlace } from "@/lib/route";
import { TabPanel, TabRow } from "@/kernel/tabs";
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
  const [act, setAct] = useState<HTMLElement | null>(null);
  const search = views[view].search;
  const headed = views[view].ownsHeader === true;
  const registered = (
    <PageActs host={act}>
      <Registered key={key} view={view} views={views} place={merged} onPlace={record} />
    </PageActs>
  );
  return (
    <Pane>
      <Page>
        {headed ? null : (
          <PageHeader
            title={title}
            search={
              search ? (
                <PaneSearch
                  label={search}
                  query={place.q ?? ""}
                  onSearch={(q) => record({ q: q || undefined, after: undefined })}
                />
              ) : null
            }
            action={<span ref={setAct} className="contents" />}
          />
        )}
        {tabs.length > 1 ? (
          <>
            <PageToolbar>
              <TabRow
                group={group}
                tabs={tabs}
                current={view}
                label={(name) => views[name].label}
                onPick={(name) => onPlace(name, {}, "push")}
              />
            </PageToolbar>
            <TabPanel
              group={group}
              current={view}
              className={BANDS}
              data-testid={group}
            >
              {registered}
            </TabPanel>
          </>
        ) : (
          <div className={BANDS} data-testid={group}>
            {registered}
          </div>
        )}
      </Page>
    </Pane>
  );
}

/** What the member has typed is theirs until they submit it, so the box holds its own text and
 *  takes the place's word for it only when the place itself names a different query — a filter
 *  picked beside a half-written search leaves that search standing. */
function PaneSearch({
  label,
  query,
  onSearch,
}: {
  label: string;
  query: string;
  onSearch: (query: string) => void;
}) {
  const [typed, setTyped] = useState(query);
  useEffect(() => setTyped(query), [query]);
  return (
    <Search
      label={label}
      placeholder="Search"
      value={typed}
      onChange={(event) => setTyped(event.target.value)}
      onSubmit={() => onSearch(typed.trim())}
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
