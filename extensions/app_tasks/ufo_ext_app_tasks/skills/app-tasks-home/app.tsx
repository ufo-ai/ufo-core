// The tasks app's page: a static site built with the portal's app kit. Edit this file and redeploy
// to change the page.

import {
  Header,
  HeldRecords,
  ObjectPane,
  SectionApp,
  mountApp,
  usePageHead,
  useState,
} from "ufo/kit";
import type { Placement } from "ufo/kit";

/** What is armed to run an agent when nobody is typing: a clock, or a source that changed. The two
 *  are one destination because a member asking what stands ready here asks one question, and an
 *  answer split across two screens is one they have to know to look for twice.
 *
 *  They stand as two listings rather than one, because they are two kinds with almost nothing in
 *  common to put in a column — a schedule and a next run against a source and where it came in.
 *  Merged, every row would carry a column the other kind leaves empty; stacked, each keeps the
 *  columns, order, search and pager its own kind declares, and the page still answers the one
 *  question whole. Neither needs a control to reach it: they are both already on the screen.
 *
 *  What those standing orders have already done is the radar's answer, not this one: a member here
 *  is reading or changing what will happen, not what did. */
const KINDS = [
  { kind: "scheduled_task", label: "Scheduled" },
  { kind: "source_trigger", label: "Triggers" },
];

/** The workspace's standing orders. The page owns the track and draws the records on it, because
 *  two listings stand here and a record either of them opens is the page's — a listing drawing the
 *  track would put a second node on an id the other listing had already put one on. A record the
 *  route names — which is what search hands over — stands on that same track, and its lane names
 *  the app whose namespace it lives in, since the kinds cross apps.
 *
 *  Closing a record reads the listings again, so a row that record deleted or changed is stated as
 *  it now is rather than as it was when the member opened it. */
function Tasks({
  title,
  place,
  onPlace,
}: {
  title: string;
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const [generation, setGeneration] = useState(0);
  const opens = place.opens ?? [];
  const band = usePageHead(<Header pinned heading={1} title={title} />);
  return (
    <>
      {band}
      {KINDS.map((held) => (
        <ObjectPane
          key={held.kind + "/" + generation}
          agentId={null}
          kind={held.kind}
          section={held.label}
          opens={opens}
          onPlace={onPlace}
        />
      ))}
      <HeldRecords
        opens={opens}
        onPlace={onPlace}
        onShut={() => setGeneration((count) => count + 1)}
      />
    </>
  );
}

mountApp(document.getElementById("root")!, (init) => (
  <SectionApp
    tab="tasks"
    init={init}
    view={{
      label: "Tasks",
      remountOnPlace: false,
      ownsHeader: true,
      render: (place, onPlace) => <Tasks title="Tasks" place={place} onPlace={onPlace} />,
    }}
  />
));
