import { useEffect, useState } from "react";

import { Search } from "@/components/ui/field";
import type { Placement } from "@/kernel/pager";
import { BANDS, COLUMN, Page, PageActs, PageHeader, Pane } from "@/kernel/pane";
import { usePlaceRecorder } from "@/kernel/place";
import { cn } from "@/lib/cn";
import type { PlaceStep, WorkspacePlace } from "@/lib/route";
import type { PaneView } from "@/views/registry";

/** A destination the sidebar reaches: a tab array and one registry. Workspace, Customize and each
 *  top-level section differ in nothing else, so they are one shell — a second copy would be a
 *  second answer to what opening a row does to history. The band names the view the member is on
 *  and moves between the views beside it, so there is no strip: a destination's own name and the
 *  way out of it are one control rather than a heading with a row of pills repeating it. The
 *  header carries the search over the whole destination; the filter that narrows the records to a
 *  family stays with them.
 *
 *  Everything the shell draws stands in one `COLUMN`, the measure a conversation is read at. The
 *  heading, the controls and the records share it, so the page reads as one column rather than as
 *  a title stranded at an edge above centred records — and a member moving between a conversation
 *  and a destination finds the column where they left it. */
export function TabbedPane<Tab extends string>({
  group,
  tabs,
  views,
  view,
  place,
  onPlace,
}: {
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
        <div className={cn(COLUMN, BANDS)}>
        {headed ? null : (
          <PageHeader
            title={views[view].label}
            siblings={tabs.map((name) => ({ label: views[name].label, value: name }))}
            onPick={(name) => onPlace(name as Tab, {}, "push")}
            search={
              search ? (
                <PaneSearch
                  key={view}
                  label={search}
                  query={place.q ?? ""}
                  onSearch={(q) => record({ q: q || undefined, after: undefined })}
                />
              ) : null
            }
            action={<span ref={setAct} className="contents" />}
          />
        )}
        <div className={BANDS} data-testid={group}>
          {registered}
        </div>
        </div>
      </Page>
    </Pane>
  );
}

/** What the member has typed is theirs until they submit it, so the box holds its own text and
 *  takes the place's word for it only when the place itself names a different query — a filter
 *  picked beside a half-written search leaves that search standing. It is held only as long as the
 *  view it was typed against: one shell heads every tab and every section from the same box, and a
 *  term left unsubmitted on one of them would otherwise still be standing in it on the next, where
 *  Enter would narrow records it was never meant for. */
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
