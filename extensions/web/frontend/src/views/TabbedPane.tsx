import { useEffect, useState } from "react";

import { Search } from "@/components/ui/field";
import type { Placement } from "@/kernel/pager";
import { Segmented } from "@/components/ui/filter";
import {
  BANDS,
  Banded,
  COLUMN,
  Header,
  Page,
  PageActs,
  PageHead,
  PageSearch,
  Pane,
} from "@/kernel/pane";
import { usePlaceRecorder } from "@/kernel/place";
import { cn } from "@/lib/cn";
import type { PlaceStep, WorkspacePlace } from "@/lib/route";
import type { Crumb } from "@/lib/title";
import type { PaneView } from "@/views/registry";

export function TabbedPane<Tab extends string>({
  group,
  views,
  tabs,
  view,
  crumb,
  banded = false,
  place,
  onPlace,
}: {
  group: string;
  tabs: readonly Tab[];
  views: Record<Tab, PaneView>;
  view: Tab;
  crumb?: Crumb;
  banded?: boolean;
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
  const [head, setHead] = useState<HTMLElement | null>(null);
  const search = views[view].search;
  const headed = views[view].ownsHeader === true;
  return (
    <Banded value={banded}>
      <Pane opens={merged.opens ?? []} onMove={(opens) => record({ opens })}>
        {headed ? (
          <div ref={setHead} className="contents" />
        ) : (
          <Header
            pinned
            heading={1}
            crumb={crumb}
            title={views[view].label}
            acts={<span ref={setAct} className="contents" />}
            bar={
              tabs.length > 1 ? (
                <Segmented
                  label={group}
                  segments={tabs.map((name) => ({ label: views[name].label, value: name }))}
                  value={view}
                  onPick={(name) => onPlace(name as Tab, {}, "push")}
                />
              ) : null
            }
          />
        )}
        <Page>
          <div className={cn(headed ? BANDS : cn(COLUMN, BANDS))}>
            <div className={BANDS} data-testid={group}>
              <PageSearch node={search ? (
                <PaneSearch
                  key={view}
                  label={search}
                  query={place.q ?? ""}
                  onSearch={(q) => record({ q: q || undefined, after: undefined })}
                />
              ) : null}>
              <PageHead host={head}>
                <PageActs host={act}>
                  <Registered key={key} view={view} views={views} place={merged} onPlace={record} />
                </PageActs>
              </PageHead>
              </PageSearch>
            </div>
          </div>
        </Page>
      </Pane>
    </Banded>
  );
}

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
