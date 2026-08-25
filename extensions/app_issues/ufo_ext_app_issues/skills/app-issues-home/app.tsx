// The issues app's page: a static site built with the portal's app kit. Edit this file and
// redeploy to change the page.
//
// It ships filled in. Every row below is placeholder written to read exactly like the real thing,
// because this file is the shape the app rebuilds against its own tracker on the first Build app.

import {
  AppConversations,
  Avatar,
  AvatarFallback,
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
import type { Placement, ReactNode } from "ufo/kit";

const APP = "Issues";

const PURPOSE =
  "Gives each new issue the member who should own it and a plan for it, and can implement the "
  + "ones you approve.";

const STANDING = "Last triage 08:12 · 6 triaged today · 3 approved to implement · 1 pull request open";

/** The label a member puts on an issue to approve implementing it. Approval is readable on the
 *  issue itself by whoever opens it next, which is why it is a label and not a word in chat. */
const IMPLEMENT_LABEL = "ufo:implement";

type Person = { name: string; initials: string };

type Triaged = {
  ref: string;
  title: string;
  owner: Person;
  kind: string;
  asking: string;
  plan: string[];
  unsettled?: string;
};

/** Issues the app has read and answered on: what it is really asking for, who should own it, and
 *  the plan it would follow. */
const TRIAGED: Triaged[] = [
  {
    ref: "#2040",
    title: "SSO for enterprise plans",
    owner: { name: "Rae Whitlock", initials: "RW" },
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
    owner: { name: "Ines Okafor", initials: "IO" },
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
    owner: { name: "Cleo Marsh", initials: "CM" },
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

/** What a member has approved, and how far it has got. */
const QUEUE: Queued[] = [
  {
    ref: "#2042",
    title: "First run offers one button",
    owner: { name: "Ines Okafor", initials: "IO" },
    state: "Pull request open",
    detail: "#2077 — one button, copy still to write",
  },
  {
    ref: "#2051",
    title: "Sign-in loop on an expired session",
    owner: { name: "Cleo Marsh", initials: "CM" },
    state: "Working",
    detail: "Started 08:12",
  },
  {
    ref: "#2033",
    title: "Portal reads stale app names after a rename",
    owner: { name: "Rae Whitlock", initials: "RW" },
    state: "Approved",
    detail: "Waiting for the next sweep",
  },
];

const TRIAGE_TITLE = "Triaged";
const QUEUE_TITLE = "Approved to implement";
const QUEUE_NOTE = `An issue carrying ${IMPLEMENT_LABEL} is picked up on the next sweep.`;
const CONVERSATIONS = "Conversations";
const NO_CONVERSATIONS = "Your chats with this app, and every run it makes on its own, land here.";

const ASKING = "Asking for";
const PLAN = "Plan";
const UNSETTLED = "Unsettled";

function Who({ person }: { person: Person }) {
  return (
    <Avatar title={person.name} className="size-(--size-glyph)">
      <AvatarFallback className="text-small">{person.initials}</AvatarFallback>
    </Avatar>
  );
}

function Line({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-lg">
      <span className="w-hint shrink-0 text-label text-ink-soft">{label}</span>
      <div className="min-w-0 flex-1 text-label">{children}</div>
    </div>
  );
}

function TriagedIssue({ issue }: { issue: Triaged }) {
  return (
    <article className="flex flex-col gap-lg border-t border-edge py-xl">
      <header className="flex items-baseline gap-lg">
        <span className="font-mono text-label text-ink-soft">{issue.ref}</span>
        <h3 className="m-0 flex-1 text-body font-medium">{issue.title}</h3>
        <span className="rounded-control bg-fill px-sm py-2xs text-small">{issue.kind}</span>
        <Who person={issue.owner} />
      </header>
      <Line label={ASKING}>{issue.asking}</Line>
      <Line label={PLAN}>
        <ul className="m-0 flex list-disc flex-col gap-2xs pl-xl">
          {issue.plan.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ul>
      </Line>
      {issue.unsettled ? <Line label={UNSETTLED}>{issue.unsettled}</Line> : null}
      <div className="flex gap-xs">
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
  // The one record a row opens stands beside the page, and closing it clears the track.
  const held = place.opens?.at(-1) ?? null;
  const at = held === null ? null : objectAt(held);
  const band = usePageHead(
    <Header pinned heading={1} title={APP} lede={PURPOSE} note={STANDING} />,
  );
  return (
    <>
      {band}
      <Section title={QUEUE_TITLE} note={QUEUE_NOTE}>
        <table className="w-full border-collapse text-left">
          <tbody>
            {QUEUE.map((item) => (
              <tr key={item.ref} className="border-t border-edge">
                <td className="py-lg pr-lg align-top font-mono text-small text-ink-soft">
                  {item.ref}
                </td>
                <td className="py-lg pr-lg align-top text-label">{item.title}</td>
                <td className="py-lg pr-lg align-top">
                  <Who person={item.owner} />
                </td>
                <td className="py-lg pr-lg align-top text-small">{item.state}</td>
                <td className="py-lg align-top text-small text-ink-soft">{item.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
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
