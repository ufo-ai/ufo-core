// The metrics app's page: a static site built with the portal's app kit. Edit this file and
// redeploy to change the page.
//
// It ships filled in. Every number below is placeholder written to read exactly like the real
// thing, because this file is the shape the app rebuilds against its own sources on the first
// Build app. Each measure states the rule it was counted by: a number whose rule nobody can state
// is a number nobody can act on.

import {
  AppConversations,
  Button,
  Header,
  ObjectDetail,
  PanelEmpty,
  Section,
  SectionApp,
  compose,
  mountApp,
  objectAt,
  slotOf,
  usePageHead,
} from "ufo/kit";
import type { Placement } from "ufo/kit";

const APP = "Metrics";

const PURPOSE =
  "Reports how delivery is going on a cadence you pick, and can report revenue and support "
  + "beside it.";

const STANDING = "Last report Mon 09:00 · week of 18–24 Aug · next Mon 09:00";

type Measure = {
  label: string;
  value: string;
  move: string;
  rising: boolean;
  rule: string;
};

type MeasureSet = {
  title: string;
  note: string;
  armed: boolean;
  ask: string;
  measures: Measure[];
};

/** One set per subject. An unarmed set states what it would report and carries the ask that turns
 *  it on, so the app states its whole capability rather than burying it in a prompt. */
const SETS: MeasureSet[] = [
  {
    title: "Engineering",
    note: "Week of 18–24 Aug, against the week before.",
    armed: true,
    ask: "",
    measures: [
      {
        label: "Shipped",
        value: "23",
        move: "+4",
        rising: true,
        rule: "counted as pull requests merged into the default branch",
      },
      {
        label: "Median time to merge",
        value: "9h 40m",
        move: "−2h 10m",
        rising: false,
        rule: "counted from first commit pushed to merge, excluding drafts",
      },
      {
        label: "Reverted",
        value: "1",
        move: "+1",
        rising: true,
        rule: "counted as merges followed by a revert within seven days",
      },
      {
        label: "Open past a week",
        value: "6",
        move: "−2",
        rising: false,
        rule: "counted as open pull requests whose first commit is over seven days old",
      },
    ],
  },
  {
    title: "Revenue",
    note: "Not on. Ask me to report revenue and it lands beside delivery.",
    armed: false,
    ask: "Report revenue every Monday morning.",
    measures: [
      {
        label: "Committed",
        value: "$48,000",
        move: "+$12,000",
        rising: true,
        rule: "counted as annual contract value signed in the period",
      },
      {
        label: "Renewing in 30 days",
        value: "2",
        move: "0",
        rising: true,
        rule: "counted as contracts whose end date falls inside the next thirty days",
      },
    ],
  },
  {
    title: "Support",
    note: "Not on. Ask me to report support and it lands beside the rest.",
    armed: false,
    ask: "Report support every Monday morning.",
    measures: [
      {
        label: "Opened",
        value: "31",
        move: "−9",
        rising: false,
        rule: "counted as conversations a member started in the period",
      },
      {
        label: "Median first reply",
        value: "42m",
        move: "−11m",
        rising: false,
        rule: "counted from the member's first message to the first human reply",
      },
    ],
  },
];

const CONVERSATIONS = "Conversations";
const NO_CONVERSATIONS = "Your chats with this app, and every run it makes on its own, land here.";

function Cell({ measure, armed }: { measure: Measure; armed: boolean }) {
  return (
    <div
      className={
        armed
          ? "flex min-w-(--container-tile) flex-1 flex-col gap-2xs py-lg"
          : "flex min-w-(--container-tile) flex-1 flex-col gap-2xs py-lg opacity-60"
      }
    >
      <span className="text-label text-ink-soft">{measure.label}</span>
      <span className="flex items-baseline gap-xs">
        <span className="font-mono text-title">{measure.value}</span>
        <span className={measure.rising ? "text-small text-attention-ink" : "text-small text-ink-soft"}>
          {measure.move}
        </span>
      </span>
      <span className="max-w-hint text-small text-ink-soft">{measure.rule}</span>
    </div>
  );
}

function Measures({ set }: { set: MeasureSet }) {
  return (
    <Section
      title={set.title}
      note={set.note}
      action={
        set.armed ? null : (
          <Button variant="outline" size="bar" onClick={() => compose(set.ask)}>
            Turn on
          </Button>
        )
      }
    >
      <div className="flex flex-wrap gap-3xl">
        {set.measures.map((measure) => (
          <Cell key={measure.label} measure={measure} armed={set.armed} />
        ))}
      </div>
    </Section>
  );
}

function Home({
  agentId,
  place,
  onPlace,
}: {
  agentId: string;
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  // The one record a row opens stands beside the page, and closing it clears the track.
  const held = place.opens?.at(-1) ?? null;
  const at = held === null ? null : objectAt(held);
  const band = usePageHead(
    <Header pinned heading={1} title={APP} lede={PURPOSE} note={STANDING} />,
  );
  return (
    <>
      {band}
      {SETS.map((set) => (
        <Measures key={set.title} set={set} />
      ))}
      <AppConversations
        agentId={agentId}
        title={CONVERSATIONS}
        blank={NO_CONVERSATIONS}
        place={place}
        onPlace={onPlace}
      />
      {held !== null && at === null ? (
        <PanelEmpty>That item is not on this page.</PanelEmpty>
      ) : at === null ? null : (
        <ObjectDetail
          key={held}
          agentId={at.agent}
          kind={at.kind}
          name={at.name}
          onOpen={(next) => onPlace({ ...place, opens: [slotOf(next)] })}
          onBack={() => onPlace({ ...place, opens: undefined })}
        />
      )}
    </>
  );
}

mountApp(document.getElementById("root")!, (init) => (
  <SectionApp
    tab="metrics"
    init={init}
    view={{
      label: APP,
      remountOnPlace: false,
      ownsHeader: true,
      render: (place, onPlace) => (
        <Home agentId={init.agentId} place={place} onPlace={onPlace} />
      ),
    }}
  />
));
