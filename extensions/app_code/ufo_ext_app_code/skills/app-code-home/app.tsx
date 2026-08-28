// The Code app's page: a static site built with the portal's app kit. Edit this file and redeploy
// to change the page.
//
// It ships filled in. Every row below is placeholder written to read exactly like the real thing,
// because this file is the shape the app rebuilds against its own repository on the first Build
// app.

import {
  AppConversations,
  AvatarStack,
  Header,
  ObjectDetail,
  PanelEmpty,
  Section,
  SectionApp,
  mountApp,
  objectAt,
  slotOf,
  usePageHead,
} from "ufo/kit";
import type { Placement } from "ufo/kit";

const APP = "Code";

const PURPOSE =
  "Reviews each pull request as it changes, and says what would break and what is missing.";

const STANDING = "Last review 08:21 · 4 open · 2 reviewed at head · 3 findings standing";

type Person = { name: string; email: string };

/** The impacts a finding may carry, exactly as the review procedure lists them. A reviewer that
 *  cannot name one of these does not report the defect at all. */
type Impact =
  | "Security or workspace-boundary breach"
  | "Data loss, corruption, or wrong-target mutation"
  | "Production outage, deadlock, or permanently unfinished work"
  | "A supported operation fails or cannot complete for valid input"
  | "Materially incorrect result or state for a supported workflow";

type Finding = { impact: Impact; where: string; says: string };

type Review = {
  ref: string;
  title: string;
  author: Person;
  head: string;
  state: "Reviewed" | "Reviewing";
  findings: Finding[];
};

/** The pull requests this app is tracking, newest head first. One conversation tracks one pull
 *  request, and a review runs per head SHA — a revision caused only by timestamps or a base merge
 *  starts nothing. */
const REVIEWS: Review[] = [
  {
    ref: "#2077",
    title: "First run offers one button",
    author: { name: "Ines Okafor", email: "ines@metalcraft.test" },
    head: "a41c9e2",
    state: "Reviewed",
    findings: [
      {
        impact: "A supported operation fails or cannot complete for valid input",
        where: "extensions/web/frontend/src/views/FirstRun.tsx:180",
        says: "The one button starts the workspace before the member has a seat, so an unseated "
          + "member reaches a refusal with no way back.",
      },
      {
        impact: "Security or workspace-boundary breach",
        where: "extensions/web/frontend/src/views/FirstRun.tsx:214",
        says: "The seat check reads the invited address rather than the signed-in one, so a "
          + "member joins the workspace named in the link instead of their own.",
      },
    ],
  },
  {
    ref: "#2071",
    title: "Clear the stale session cookie on refusal",
    author: { name: "Cleo Marsh", email: "cleo@metalcraft.test" },
    head: "7be0d13",
    state: "Reviewed",
    findings: [
      {
        impact: "Materially incorrect result or state for a supported workflow",
        where: "core/src/ufo/sdk/http.py:96",
        says: "The clear drops the cookie without its original path, so the browser keeps the old "
          + "one and the loop stands.",
      },
    ],
  },
  {
    ref: "#2069",
    title: "Speed app switching",
    author: { name: "Rae Whitlock", email: "rae@metalcraft.test" },
    head: "1d55af0",
    state: "Reviewing",
    findings: [],
  },
  {
    ref: "#2064",
    title: "Add the sandbox tool bridge",
    author: { name: "Rae Whitlock", email: "rae@metalcraft.test" },
    head: "c0a7742",
    state: "Reviewed",
    findings: [],
  },
];

const QUEUE = "Review queue";
const CONVERSATIONS = "Conversations";
const NO_CONVERSATIONS = "Your chats with this app, and every run it makes on its own, land here.";

function Pull({ review }: { review: Review }) {
  return (
    <article className="flex flex-col gap-sm border-t border-edge py-xl">
      <header className="flex items-baseline gap-sm">
        <span className="font-mono text-label text-ink-soft">{review.ref}</span>
        <h3 className="m-0 flex-1 text-body font-medium">{review.title}</h3>
        <span className="font-mono text-small text-ink-soft">{review.head}</span>
        <AvatarStack people={[review.author]} />
      </header>
      <div className="flex items-baseline gap-sm">
        <span className="w-hint shrink-0 text-label text-ink-soft">{review.state}</span>
        <span className="text-small text-ink-soft">
          {review.findings.length === 0
            ? "Nothing standing at this head."
            : `${review.findings.length} standing`}
        </span>
      </div>
      {review.findings.map((finding) => (
        <div key={finding.where} className="flex flex-col gap-2xs border-l border-edge pl-lg">
          <span className="flex items-baseline gap-2xs">
            <span className="rounded-control bg-fill px-sm py-2xs text-small">
              {finding.impact}
            </span>
            <span className="font-mono text-small text-ink-soft">{finding.where}</span>
          </span>
          <span className="max-w-hint text-label">{finding.says}</span>
        </div>
      ))}
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
  // The one record a row opens stands beside the page, and closing it clears the track.
  const held = place.opens?.at(-1) ?? null;
  const at = held === null ? null : objectAt(held);
  const band = usePageHead(
    <Header pinned heading={1} title={APP} lede={PURPOSE} note={STANDING} />,
  );
  return (
    <>
      {band}
      <Section title={QUEUE}>
        <div className="flex flex-col">
          {REVIEWS.map((review) => (
            <Pull key={review.ref} review={review} />
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
    tab="code"
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
