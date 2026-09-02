// The metrics app's page: a static site built with the portal's app kit. Edit this file and
// redeploy to change the page.
//
// It ships filled in. Every number below is placeholder written to read exactly like the real
// thing, because this file is the shape the app rebuilds against its own sources on the first
// Build app. Each measure states the rule it was counted by: a number whose rule nobody can state
// is a number nobody can act on.

import {
  AppConversations,
  AvatarStack,
  Badge,
  BrandMark,
  Breakdown,
  BreakdownHeader,
  BreakdownLabel,
  BreakdownMark,
  BreakdownName,
  BreakdownRow,
  BreakdownRows,
  BreakdownValue,
  Button,
  Card,
  CardAction,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
  Chart,
  ChartBars,
  Header,
  IconChevronDown,
  Legend,
  LegendItem,
  Meter,
  ObjectDetail,
  PanelEmpty,
  Section,
  SectionApp,
  Separator,
  Stat,
  StatDelta,
  StatDescription,
  StatHeader,
  StatLabel,
  StatMedia,
  StatValue,
  compose,
  mountApp,
  objectAt,
  slotOf,
  usePageHead,
} from "ufo/kit";
import type { AvatarStackPerson, ChartBar, MeterPart, Placement } from "ufo/kit";

const APP = "Metrics";

const PURPOSE =
  "Reports delivery, revenue, runway, reliability, product and support on a cadence you pick, "
  + "from the accounts you connect.";

const STANDING = "Last report Mon 09:00 · week of 18–24 Aug · next Mon 09:00";

const GRID = "grid grid-cols-4 gap-6xl max-narrow:grid-cols-1";
const THIRDS = "grid grid-cols-3 gap-6xl max-narrow:grid-cols-1";
const CARDS = "grid grid-cols-3 gap-2xl max-narrow:grid-cols-1";
const BAND = "grid grid-cols-3 items-start gap-6xl py-2xl max-narrow:grid-cols-1";
const HEAD = "flex h-(--size-glyph) w-full items-center gap-2xl";
const PLOT_HEAD = "flex h-(--size-glyph) w-full items-center gap-2xs";
const BAND_TITLE = "min-w-0 flex-1 truncate text-label font-medium text-ink-soft";

type Connector = { provider: string; name: string };

const GITHUB: Connector = { provider: "github", name: "GitHub" };
const SLACK: Connector = { provider: "slack", name: "Slack" };
const STRIPE: Connector = { provider: "stripe", name: "Stripe" };
const QUICKBOOKS: Connector = { provider: "quickbooks", name: "QuickBooks" };

type Measure = {
  label: string;
  value: string;
  move: string;
  direction: "up" | "down" | "default";
  rule: string;
  source?: Connector;
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
        direction: "up",
        rule: "counted as pull requests merged into the default branch",
        source: GITHUB,
      },
      {
        label: "Median time to merge",
        value: "9h 40m",
        move: "−2h 10m",
        direction: "down",
        rule: "counted from first commit pushed to merge, excluding drafts",
        source: GITHUB,
      },
      {
        label: "Reverted",
        value: "1",
        move: "+1",
        direction: "up",
        rule: "counted as merges followed by a revert within seven days",
        source: GITHUB,
      },
      {
        label: "Open past a week",
        value: "6",
        move: "−2",
        direction: "down",
        rule: "counted as open pull requests whose first commit is over seven days old",
        source: GITHUB,
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
        direction: "up",
        rule: "counted as annual contract value signed in the period",
      },
      {
        label: "Renewing in 30 days",
        value: "2",
        move: "0",
        direction: "default",
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
        direction: "down",
        rule: "counted as conversations a member started in the period",
      },
      {
        label: "Median first reply",
        value: "42m",
        move: "−11m",
        direction: "down",
        rule: "counted from the member's first message to the first human reply",
      },
    ],
  },
];

const MERGED_LABEL = "Pull requests merged each day since 14 July";
const MERGED_FROM = "14 Jul";
const MERGED_TO = "24 Aug";
const MERGED_RULE =
  "One column a day, counted the same way as Shipped. The reported week is drawn apart from the "
  + "five before it, and a day nothing merged draws the shortest column rather than a gap.";

/** Six weeks a day at a time — the run Figma draws, at the column and gap it draws them with. The
 *  band says which week a day belongs to, so the reported week reads against the five it followed
 *  without a second chart beside it. */
const MERGED: ChartBar[] = [
  ...[2, 5, 3, 4, 1, 0, 2].map((value) => ({ value, tone: "muted" as const })),
  ...[3, 6, 2, 5, 4, 0, 1].map((value) => ({ value, tone: "muted" as const })),
  ...[4, 2, 7, 3, 5, 0, 2].map((value) => ({ value, tone: "muted" as const })),
  ...[5, 4, 2, 6, 3, 1, 4].map((value) => ({ value, tone: "muted" as const })),
  ...[5, 3, 1, 6, 4, 1, 3].map((value) => ({ value, tone: "secondary" as const })),
  ...[4, 6, 2, 5, 3, 0, 3].map((value) => ({ value, tone: "primary" as const })),
];

const QUEUE_LABEL = "Open pull requests at the end of each day";
const QUEUE = [11, 12, 10, 13, 14, 12, 9, 8, 10, 9, 7, 8, 6, 6];

const USAGE_TITLE = "Usage";
const USAGE_NOTE = "Runs and spend for the week of 18–24 Aug. Every app in this workspace.";
const USAGE_ASK = "Break usage down by app for the last four weeks.";
const CONNECTORS_LABEL = "Connectors";
const CONNECTORS_MORE = "By share of runs";

type Share = { provider: string; name: string; share: string };

/** Which connectors the week's runs actually reached. Share of runs and not of spend: a run that
 *  read one issue and a run that read a thousand cost differently, and this states reach. */
const SHARES: Share[] = [
  { provider: "github", name: "GitHub", share: "41.2%" },
  { provider: "slack", name: "Slack", share: "22.8%" },
  { provider: "googlecalendar", name: "Google Calendar", share: "14.1%" },
  { provider: "notion", name: "Notion", share: "9.6%" },
  { provider: "stripe", name: "Stripe", share: "7.0%" },
  { provider: "linear", name: "Linear", share: "5.3%" },
];

const RUNS_LABEL = "Runs each day this week";
const RUNS = [38, 44, 31, 52, 47, 6, 41];

type Allowance = {
  label: string;
  value: string;
  move: string;
  parts: readonly MeterPart[];
  of: number;
  rule: string;
};

/** What the workspace has used of what it is allowed. Drawn as cells rather than a length, because
 *  a member reads four of six off the bar and reads nothing off two thirds of a stripe. */
const ALLOWANCES: Allowance[] = [
  {
    label: "Runs this month",
    value: "412",
    move: "+64",
    parts: [
      { label: "Scheduled", value: 318, tone: "primary" },
      { label: "Asked for", value: 94, tone: "secondary" },
    ],
    of: 1000,
    rule: "counted as turns a scheduled app started on its own, against the plan's monthly cap",
  },
  {
    label: "Spend this month",
    value: "$31.40",
    move: "+$8.10",
    parts: [
      { label: "Models", value: 2680, tone: "primary" },
      { label: "Sandboxes", value: 460, tone: "secondary" },
    ],
    of: 10000,
    rule: "counted as model spend the ledger recorded, against the workspace cap an admin set",
  },
];

const SIGNED_TITLE = "Signed each day";
const SIGNED_TREND = "Trending";
const SIGNED_LABEL = "Annual contract value signed each day since 28 July";
const SIGNED_FROM = "28 Jul";
const SIGNED_TO = "24 Aug";
const SIGNED_RULE =
  "One column a day, counted as the annual contract value signed that day. Last week and this "
  + "week are each drawn apart from the weeks before them, and a day nothing was signed draws the "
  + "shortest column rather than a gap.";

/** Four weeks a day at a time, in thousands. Contracts do not sign daily, so most days are empty
 *  and the four weeks read as their totals: 20, 28, 23, and the 48 the week reports. */
const SIGNED: ChartBar[] = [
  ...[0, 8, 0, 0, 12, 0, 0].map((value) => ({ value, tone: "muted" as const })),
  ...[6, 0, 0, 18, 0, 0, 4].map((value) => ({ value, tone: "muted" as const })),
  ...[0, 0, 14, 0, 9, 0, 0].map((value) => ({ value, tone: "secondary" as const })),
  ...[12, 0, 0, 24, 0, 0, 12].map((value) => ({ value, tone: "primary" as const })),
];

const SOURCES_TITLE = "By source, week on week";
const FILTER = "Filter";
const COMMITTED_LABEL = "Committed each week since 2 June";
const COMMITTED = [9, 16, 22, 12, 31, 14, 18, 26, 20, 28, 23, 48];

type Source = { provider: string; name: string; move: string };

/** Where the week's contracts were signed, and how each source moved against the week before.
 *  Three sources and not six: a source under a point of movement is a row a member reads past. */
const SOURCES: Source[] = [
  { provider: "stripe", name: "Stripe", move: "+2.3%" },
  { provider: "quickbooks", name: "QuickBooks", move: "−3.2%" },
  { provider: "hubspot", name: "HubSpot", move: "+0.3%" },
];

type Target = {
  title: string;
  figure: string;
  move: string;
  label: string;
  parts: readonly MeterPart[];
  of: number;
  people: AvatarStackPerson[];
};

/** The two wholes revenue is read against: the quarter's target and the book up for renewal. The
 *  figure over each bar is the share the first part has taken, so the bar and the number state one
 *  thing, and the faces under it are the members those contracts sit with. */
const TARGETS: Target[] = [
  {
    title: "Committed against target",
    figure: "28%",
    move: "+2.3%",
    label: "Committed against the quarter's target",
    parts: [
      { label: "Signed", value: 48000, tone: "primary" },
      { label: "Out for signature", value: 119000, tone: "secondary" },
    ],
    of: 170000,
    people: [
      { name: "Rae Whitlock" },
      { name: "Ines Okafor" },
      { name: "Tomas Ferrer" },
      { name: "Cleo Marsh" },
    ],
  },
  {
    title: "Renewing against the book",
    figure: "25%",
    move: "+0.4%",
    label: "Renewals against the book up for renewal",
    parts: [
      { label: "Renewed", value: 22000, tone: "primary" },
      { label: "Awaiting a reply", value: 62000, tone: "secondary" },
    ],
    of: 88000,
    people: [
      { name: "Cleo Marsh" },
      { name: "Tomas Ferrer" },
      { name: "Rae Whitlock" },
      { name: "Ines Okafor" },
    ],
  },
];

const NEEDS_TITLE = "What I still need";
const NEEDS_NOTE = "Four things nobody has told me yet. Each one turns a measure on.";
const NEEDS_ASK = "Walk me through what you still need.";

type Need = {
  title: string;
  state: string | null;
  says: string;
  act: string;
  ask: string;
  wires: Connector[];
};

/** Each card states one prerequisite and what answering it turns on, so the page states its whole
 *  capability rather than leaving a member to guess which numbers are missing and why. */
const NEEDS: Need[] = [
  {
    title: "Name where support happens",
    state: "Recommended",
    says:
      "Slack is connected; Zendesk and Intercom are not. Name the channel or the shared inbox "
      + "customers write to and the support set turns on.",
    act: "Name it",
    ask: "Support happens in ",
    wires: [SLACK],
  },
  {
    title: "Split the spend by category",
    state: "Recommended",
    says:
      "Every dollar since June lands in one uncategorised account, so burn is one figure and "
      + "cannot be broken down.",
    act: "Connect",
    ask: "Split the spend by category.",
    wires: [QUICKBOOKS],
  },
  {
    title: "Start billing customers",
    state: null,
    says:
      "Stripe holds no subscription and no payment since 1 June, so recurring revenue reads zero "
      + "rather than unknown.",
    act: "Connect",
    ask: "Start billing customers on Stripe.",
    wires: [STRIPE],
  },
  {
    title: "Decide what a fix is",
    state: "New",
    says:
      "A revert is counted by its title. Nothing links a repair back to the change it repairs, so "
      + "a fix that is not called a revert is not counted.",
    act: "Decide",
    ask: "A fix is a pull request that ",
    wires: [GITHUB],
  },
];

const CONVERSATIONS = "Conversations";
const NO_CONVERSATIONS = "Your chats with this app, and every run it makes on its own, land here.";

function Tile({ measure, armed }: { measure: Measure; armed: boolean }) {
  return (
    <Stat unarmed={!armed}>
      <StatHeader>
        <StatLabel>{measure.label}</StatLabel>
        {measure.source ? (
          <StatMedia role="img" aria-label={measure.source.name} title={measure.source.name}>
            <BrandMark provider={measure.source.provider} />
          </StatMedia>
        ) : null}
      </StatHeader>
      <StatValue>
        {measure.value}
        <StatDelta tone={measure.direction}>{measure.move}</StatDelta>
      </StatValue>
      <StatDescription>{measure.rule}</StatDescription>
    </Stat>
  );
}

function Measures({ set }: { set: MeasureSet }) {
  return (
    <Section
      title={set.title}
      note={set.note}
      action={
        set.armed ? (
          <Button variant="outline" size="bar" onClick={() => compose(`Ask about ${set.title}.`)}>
            Ask
          </Button>
        ) : (
          <Button variant="outline" size="bar" onClick={() => compose(set.ask)}>
            Turn on
          </Button>
        )
      }
    >
      <div className={GRID}>
        {set.measures.map((measure) => (
          <Tile key={measure.label} measure={measure} armed={set.armed} />
        ))}
      </div>
    </Section>
  );
}

function Delivery({ set }: { set: MeasureSet }) {
  return (
    <>
      <Measures set={set} />
      <div className={THIRDS}>
        <div className="col-span-2 flex min-w-0 flex-col gap-2xl max-narrow:col-span-1">
          <ChartBars
            label={MERGED_LABEL}
            bars={MERGED}
            from={MERGED_FROM}
            to={MERGED_TO}
          />
          <Legend>
            <LegendItem tone="muted">Earlier weeks</LegendItem>
            <LegendItem tone="secondary">Last week</LegendItem>
            <LegendItem tone="primary">This week</LegendItem>
          </Legend>
          <p className="m-0 text-small text-ink-soft">{MERGED_RULE}</p>
        </div>
        <Card>
          <CardHeader>
            <CardTitle>Open pull requests</CardTitle>
          </CardHeader>
          <Chart label={QUEUE_LABEL} points={QUEUE} />
        </Card>
      </div>
    </>
  );
}

function Usage() {
  return (
    <Section
      title={USAGE_TITLE}
      note={USAGE_NOTE}
      action={
        <Button variant="outline" size="bar" onClick={() => compose(USAGE_ASK)}>
          Ask
        </Button>
      }
    >
      <div className={THIRDS}>
        <Breakdown>
          <BreakdownHeader>
            <BreakdownLabel>{CONNECTORS_LABEL}</BreakdownLabel>
            <Badge>{CONNECTORS_MORE}</Badge>
          </BreakdownHeader>
          <BreakdownRows>
            {SHARES.map((share) => (
              <BreakdownRow key={share.name}>
                <BreakdownName>
                  <BreakdownMark>
                    <BrandMark provider={share.provider} />
                  </BreakdownMark>
                  {share.name}
                </BreakdownName>
                <BreakdownValue>{share.share}</BreakdownValue>
              </BreakdownRow>
            ))}
          </BreakdownRows>
        </Breakdown>
        <Card>
          <CardHeader>
            <CardTitle>Runs</CardTitle>
          </CardHeader>
          <Chart label={RUNS_LABEL} points={RUNS} />
        </Card>
        <div className="flex min-w-0 flex-col gap-6xl">
          {ALLOWANCES.map((allowance) => (
            <Stat key={allowance.label}>
              <StatHeader>
                <StatLabel>{allowance.label}</StatLabel>
                <Badge>{`of ${allowance.of}`}</Badge>
              </StatHeader>
              <StatValue>
                {allowance.value}
                <StatDelta tone="up">{allowance.move}</StatDelta>
              </StatValue>
              <Meter label={allowance.label} parts={allowance.parts} of={allowance.of} />
              <Legend>
                {allowance.parts.map((part) => (
                  <LegendItem key={part.label} tone={part.tone}>
                    {part.label}
                  </LegendItem>
                ))}
              </Legend>
              <StatDescription>{allowance.rule}</StatDescription>
            </Stat>
          ))}
        </div>
      </div>
    </Section>
  );
}

function FilterBadge() {
  return (
    <Badge className="gap-2xs">
      {FILTER}
      <IconChevronDown aria-hidden className="size-icon" />
    </Badge>
  );
}

function Revenue({ set }: { set: MeasureSet }) {
  return (
    <>
      <Measures set={set} />
      <div className={BAND}>
        <div className="flex min-w-0 flex-col gap-2xl">
          <div className={PLOT_HEAD}>
            <span className={BAND_TITLE}>{SIGNED_TITLE}</span>
            <Badge>{SIGNED_TREND}</Badge>
          </div>
          <ChartBars label={SIGNED_LABEL} bars={SIGNED} from={SIGNED_FROM} to={SIGNED_TO} />
          <Legend>
            <LegendItem tone="muted">Earlier weeks</LegendItem>
            <LegendItem tone="secondary">Last week</LegendItem>
            <LegendItem tone="primary">This week</LegendItem>
          </Legend>
          <p className="m-0 w-full text-mono text-ink-soft">{SIGNED_RULE}</p>
        </div>
        <Breakdown>
          <BreakdownHeader>
            <BreakdownLabel>{SOURCES_TITLE}</BreakdownLabel>
            <FilterBadge />
          </BreakdownHeader>
          <div className="w-full rounded-card bg-fill">
            <Chart label={COMMITTED_LABEL} points={COMMITTED} />
          </div>
          <BreakdownRows>
            {SOURCES.map((source) => (
              <BreakdownRow key={source.name}>
                <BreakdownName>
                  <BreakdownMark>
                    <BrandMark provider={source.provider} />
                  </BreakdownMark>
                  {source.name}
                </BreakdownName>
                <BreakdownValue>{source.move}</BreakdownValue>
              </BreakdownRow>
            ))}
          </BreakdownRows>
        </Breakdown>
        <div className="flex min-w-0 flex-col gap-6xl">
          {TARGETS.map((target) => (
            <div key={target.title} className="flex min-w-0 flex-col gap-2xl">
              <div className={HEAD}>
                <span className={BAND_TITLE}>{target.title}</span>
                <FilterBadge />
              </div>
              <div className="flex w-full items-end gap-sm">
                <span className="text-subtitle font-medium text-ink">{target.figure}</span>
                <span className="text-mono text-link">{target.move}</span>
              </div>
              <Meter label={target.label} parts={target.parts} of={target.of} />
              <AvatarStack people={target.people} />
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

function Needs() {
  return (
    <Section
      title={NEEDS_TITLE}
      note={NEEDS_NOTE}
      action={
        <Button variant="outline" size="bar" onClick={() => compose(NEEDS_ASK)}>
          Ask
        </Button>
      }
    >
      <div className={CARDS}>
        {NEEDS.map((need) => (
          <Card key={need.title}>
            <CardHeader>
              <CardTitle>{need.title}</CardTitle>
              {need.state ? (
                <CardAction>
                  <Badge>{need.state}</Badge>
                </CardAction>
              ) : null}
            </CardHeader>
            <CardDescription>{need.says}</CardDescription>
            <CardFooter>
              <Button variant="outline" size="bar" onClick={() => compose(need.ask)}>
                {need.act}
              </Button>
              <span className="flex shrink-0 items-center gap-sm">
                {need.wires.map((wire) => (
                  <span key={wire.provider} role="img" aria-label={wire.name} title={wire.name}>
                    <BrandMark provider={wire.provider} />
                  </span>
                ))}
              </span>
            </CardFooter>
          </Card>
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
  const [delivery, revenue, ...rest] = SETS;
  return (
    <>
      {band}
      <Delivery set={delivery} />
      <Separator />
      <Usage />
      <Separator />
      <Revenue set={revenue} />
      {rest.map((set) => (
        <div key={set.title} className="flex flex-col gap-6xl">
          <Separator />
          <Measures set={set} />
        </div>
      ))}
      <Separator />
      <Needs />
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
