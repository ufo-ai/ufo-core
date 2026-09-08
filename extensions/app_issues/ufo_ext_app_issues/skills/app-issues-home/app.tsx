
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
  DataTable,
  Detail,
  Header,
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
  StatValue,
  Td,
  TdFact,
  compose,
  mountApp,
  objectAt,
  slotOf,
  usePageHead,
} from "ufo/kit";
import type { Placement } from "ufo/kit";

const APP = "Issues";

const PURPOSE =
  "Gives each new issue the member who should own it and a plan for it, and can implement the "
  + "ones you approve.";

const STANDING = "Last triage 08:12 · 6 triaged today · 3 approved to implement · 1 pull request open";

const IMPLEMENT_LABEL = "ufo:implement";

const MEASURES_GRID = "grid grid-cols-4 gap-6xl max-narrow:grid-cols-1";

type Person = { name: string; email: string };

type Measure = {
  label: string;
  value: string;
  move: string;
  direction: "up" | "down";
  rule: string;
};

const MEASURES: Measure[] = [
  {
    label: "Triaged",
    value: "34",
    move: "+18.5%",
    direction: "up",
    rule: "Issues the app read and answered on, a re-triage counted once.",
  },
  {
    label: "Approved",
    value: "12",
    move: "+9.1%",
    direction: "up",
    rule: `Triaged issues carrying ${IMPLEMENT_LABEL} when the sweep ran.`,
  },
  {
    label: "Median time to a plan",
    value: "6m",
    move: "-14.2%",
    direction: "down",
    rule: "From the issue opening to the plan landing on it.",
  },
];

type Share = { provider: string; name: string; share: string };

const SOURCES: Share[] = [
  { provider: "github", name: "GitHub", share: "62.4%" },
  { provider: "sentry", name: "Sentry", share: "24.1%" },
  { provider: "slack", name: "Slack", share: "13.5%" },
];

type Triaged = {
  ref: string;
  title: string;
  owner: Person;
  kind: string;
  asking: string;
  plan: string[];
  unsettled?: string;
};

const TRIAGED: Triaged[] = [
  {
    ref: "#2040",
    title: "SSO for enterprise plans",
    owner: { name: "Rae Whitlock", email: "rae@metalcraft.test" },
    kind: "Feature",
    asking: "Northstar cannot roll out without SAML; the ask is enterprise sign-in, not SSO itself.",
    plan: [
      "Add a SAML provider behind the existing session mint.",
      "Bind an org domain to a provider, one per workspace.",
      "Fall back to the current sign-in where no provider is bound.",
    ],
    unsettled: "Which identity provider they use — the issue does not say.",
  },
  {
    ref: "#2042",
    title: "First run offers one button",
    owner: { name: "Ines Okafor", email: "ines@metalcraft.test" },
    kind: "Feature",
    asking: "The first run asks four questions before it does anything useful.",
    plan: [
      "Replace the step list with one act that starts the workspace.",
      "Move what the steps collected into the first conversation.",
    ],
  },
  {
    ref: "#2051",
    title: "Sign-in loop on an expired session",
    owner: { name: "Cleo Marsh", email: "cleo@metalcraft.test" },
    kind: "Bug",
    asking: "An expired cookie redirects to sign-in, which redirects back, forever.",
    plan: [
      "Clear the stale cookie on the refusal rather than only on sign-out.",
      "Add the case to the session tests.",
    ],
  },
];

type Queued = {
  ref: string;
  title: string;
  owner: Person;
  state: "Approved" | "Working" | "Pull request open";
  detail: string;
};

const QUEUE: Queued[] = [
  {
    ref: "#2042",
    title: "First run offers one button",
    owner: { name: "Ines Okafor", email: "ines@metalcraft.test" },
    state: "Pull request open",
    detail: "#2077 — one button, copy still to write",
  },
  {
    ref: "#2051",
    title: "Sign-in loop on an expired session",
    owner: { name: "Cleo Marsh", email: "cleo@metalcraft.test" },
    state: "Working",
    detail: "Started 08:12",
  },
  {
    ref: "#2033",
    title: "Portal reads stale app names after a rename",
    owner: { name: "Rae Whitlock", email: "rae@metalcraft.test" },
    state: "Approved",
    detail: "Waiting for the next sweep",
  },
];

const WEEK_TITLE = "Triage this week";
const WEEK_NOTE = "Counted from the issues the app read since Monday. A re-triage counts once.";
const PERIOD = "vs last week";
const SOURCES_LABEL = "Where issues arrive";
const SOURCES_PERIOD = "This week";

const TRIAGE_TITLE = "Triaged";
const QUEUE_TITLE = "Approved to implement";
const QUEUE_NOTE = `An issue carrying ${IMPLEMENT_LABEL} is picked up on the next sweep.`;
const QUEUE_ASK = "What is holding up the approved issues?";
const QUEUE_ACT = "Ask";
const QUEUE_EMPTY = "Nothing is approved to implement.";
const QUEUE_COLUMNS = [
  "Issue",
  "Owner",
  { label: "State", fact: true },
  "Detail",
];
const CONVERSATIONS = "Conversations";
const NO_CONVERSATIONS = "Your chats with this app, and every run it makes on its own, land here.";

const ASKING = "Asking for";
const PLAN = "Plan";
const UNSETTLED = "Unsettled";

function TriagedIssue({ issue }: { issue: Triaged }) {
  return (
    <article className="flex flex-col gap-sm border-t border-edge py-xl">
      <header className="flex items-baseline gap-sm">
        <span className="font-mono text-label text-ink-soft">{issue.ref}</span>
        <h3 className="m-0 flex-1 text-body font-medium">{issue.title}</h3>
        <Badge>{issue.kind}</Badge>
        <AvatarStack people={[issue.owner]} />
      </header>
      <Detail label={ASKING}>{issue.asking}</Detail>
      <Detail label={PLAN}>
        <ul className="m-0 flex list-disc flex-col gap-2xs pl-xl">
          {issue.plan.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ul>
      </Detail>
      {issue.unsettled ? <Detail label={UNSETTLED}>{issue.unsettled}</Detail> : null}
      <div className="flex gap-sm">
        <Button
          variant="outline"
          size="bar"
          onClick={() => compose(`Approve ${issue.ref} for implementation.`)}
        >
          Approve
        </Button>
        <Button
          variant="outline"
          size="bar"
          onClick={() => compose(`Triage ${issue.ref} again — the plan is wrong because `)}
        >
          Re-triage
        </Button>
      </div>
    </article>
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
  const held = place.opens?.at(-1) ?? null;
  const at = held === null ? null : objectAt(held);
  const band = usePageHead(
    <Header pinned heading={1} title={APP} lede={PURPOSE} note={STANDING} />,
  );
  return (
    <>
      {band}
      <Section title={WEEK_TITLE} note={WEEK_NOTE}>
        <div className={MEASURES_GRID}>
          {MEASURES.map((measure) => (
            <Stat key={measure.label}>
              <StatHeader>
                <StatLabel>{measure.label}</StatLabel>
                <Badge>{PERIOD}</Badge>
              </StatHeader>
              <StatValue>
                {measure.value}
                <StatDelta tone={measure.direction}>{measure.move}</StatDelta>
              </StatValue>
              <StatDescription>{measure.rule}</StatDescription>
            </Stat>
          ))}
          <Breakdown>
            <BreakdownHeader>
              <BreakdownLabel>{SOURCES_LABEL}</BreakdownLabel>
              <Badge>{SOURCES_PERIOD}</Badge>
            </BreakdownHeader>
            <BreakdownRows>
              {SOURCES.map((source) => (
                <BreakdownRow key={source.name}>
                  <BreakdownName>
                    <BreakdownMark>
                      <BrandMark provider={source.provider} />
                    </BreakdownMark>
                    {source.name}
                  </BreakdownName>
                  <BreakdownValue>{source.share}</BreakdownValue>
                </BreakdownRow>
              ))}
            </BreakdownRows>
          </Breakdown>
        </div>
      </Section>
      <Separator />
      <Section
        title={QUEUE_TITLE}
        note={QUEUE_NOTE}
        action={
          <Button variant="outline" size="bar" onClick={() => compose(QUEUE_ASK)}>
            {QUEUE_ACT}
          </Button>
        }
      >
        <DataTable
          columns={QUEUE_COLUMNS}
          rows={QUEUE}
          rowKey={(item) => item.ref}
          empty={QUEUE_EMPTY}
          act={() => QUEUE_ACT}
          open={(item) => () => compose(`What is the state of ${item.ref}?`)}
        >
          {(item) => (
            <>
              <Td className="text-ink">
                <span className="flex min-w-0 items-center gap-sm">
                  <span className="shrink-0 text-ink-soft">{item.ref}</span>
                  <span className="min-w-0 truncate font-medium">{item.title}</span>
                </span>
              </Td>
              <Td>
                <AvatarStack people={[item.owner]} />
              </Td>
              <TdFact>{item.state}</TdFact>
              <Td>{item.detail}</Td>
            </>
          )}
        </DataTable>
      </Section>
      <Section title={TRIAGE_TITLE}>
        <div className="flex flex-col">
          {TRIAGED.map((issue) => (
            <TriagedIssue key={issue.ref} issue={issue} />
          ))}
        </div>
      </Section>
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
    tab="issues"
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
