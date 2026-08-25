// The meetings app's page: a static site built with the portal's app kit. Edit this file and
// redeploy to change the page.
//
// It ships filled in. Every row below is placeholder written to read exactly like the real thing —
// real names, real times, real amounts — because this file is the shape the app rebuilds against
// its own sources on the first Build app. A page of empty bands would tell the app nothing about
// what to draw, and a member nothing about what the app is for.

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

const APP = "Meetings";

/** What the app is for, in the one sentence a member reads before anything else on the page. */
const PURPOSE =
  "Briefs you before each meeting, and can turn what a meeting agreed into tracked follow-ups "
  + "and workspace notes.";

/** The line under the title: when the app last ran, and the counts a member scans for. It is the
 *  page's own status, so it says what happened rather than what the app could do. */
const STANDING = "Last brief 08:05 · 3 meetings today · 5 follow-ups open · 2 late";

type Person = { name: string; initials: string };

type Meeting = {
  at: string;
  title: string;
  attendees: Person[];
  lastTime: string;
  live: { ref: string; what: string }[];
  raise: string[];
};

/** The next meetings, each with what the app found for it: who is coming, what the last meeting
 *  with these people settled, what work is open against it, and what to raise. */
const MEETINGS: Meeting[] = [
  {
    at: "09:30",
    title: "Northstar renewal",
    attendees: [
      { name: "Rae Whitlock", initials: "RW" },
      { name: "Cleo Marsh", initials: "CM" },
    ],
    lastTime: "They asked for SSO before signing. You said the quarter after next.",
    live: [
      { ref: "#2040", what: "SSO for enterprise plans" },
      { ref: "Drive", what: "Renewal terms" },
    ],
    raise: [
      "$48,000 renews 18 September; last year they asked for annual billing.",
      "SSO is still unscheduled — say the quarter, or say it is not coming.",
      "Their support volume halved since March; worth naming.",
    ],
  },
  {
    at: "11:00",
    title: "Product design sync",
    attendees: [
      { name: "Tomas Ferrer", initials: "TF" },
      { name: "Ines Okafor", initials: "IO" },
    ],
    lastTime: "Agreed the first run offers one button, and that editing built-in apps waits.",
    live: [
      { ref: "#2042", what: "First run offers one button" },
      { ref: "Drive", what: "Design review" },
    ],
    raise: [
      "The one-button first run has no copy yet.",
      "#2042 has been open eleven days with no owner.",
      "Decide whether forking a built-in app is in scope for launch.",
    ],
  },
  {
    at: "15:30",
    title: "Investor update review",
    attendees: [{ name: "Tomas Ferrer", initials: "TF" }],
    lastTime: "Last month's update promised a 100-person waitlist number this time.",
    live: [{ ref: "Drive", what: "Investor update draft" }],
    raise: [
      "The waitlist is at 100; the draft still says 60.",
      "No revenue line yet this month.",
      "Ask whether the design partner is named or kept anonymous.",
    ],
  },
];

type FollowUp = {
  commitment: string;
  owner: Person;
  due: string;
  state: "Open" | "Late" | "Done";
  meeting: string;
};

/** What meetings committed to, and who owes it. `Late` is past its date and not done — the state a
 *  member opens this page to find. */
const FOLLOW_UPS: FollowUp[] = [
  {
    commitment: "Send Northstar the SSO timeline in writing",
    owner: { name: "Rae Whitlock", initials: "RW" },
    due: "22 Aug",
    state: "Late",
    meeting: "Northstar renewal",
  },
  {
    commitment: "Write the first-run copy for the one-button screen",
    owner: { name: "Ines Okafor", initials: "IO" },
    due: "26 Aug",
    state: "Open",
    meeting: "Product design sync",
  },
  {
    commitment: "Confirm the waitlist number for the investor update",
    owner: { name: "Tomas Ferrer", initials: "TF" },
    due: "25 Aug",
    state: "Open",
    meeting: "Investor update review",
  },
  {
    commitment: "Decide whether built-in apps can be forked at launch",
    owner: { name: "Tomas Ferrer", initials: "TF" },
    due: "21 Aug",
    state: "Late",
    meeting: "Product design sync",
  },
  {
    commitment: "Share the renewal terms doc with Cleo",
    owner: { name: "Cleo Marsh", initials: "CM" },
    due: "27 Aug",
    state: "Open",
    meeting: "Northstar renewal",
  },
];

type Note = { decision: string; meeting: string; on: string };

/** Decisions written into the workspace record, so the wiki and every later turn read them. */
const NOTES: Note[] = [
  {
    decision: "Built-in apps are not editable at launch; customising one means forking it.",
    meeting: "Product design sync",
    on: "24 Aug",
  },
  {
    decision: "The first run offers one button, not a wizard.",
    meeting: "Product design sync",
    on: "24 Aug",
  },
  {
    decision: "Northstar renewal is annual billing at $48,000.",
    meeting: "Northstar renewal",
    on: "18 Aug",
  },
];

const NEXT_MEETINGS = "Next meetings";
const FOLLOW_UPS_TITLE = "Follow-ups";
const NOTES_TITLE = "Notes";
const CONVERSATIONS = "Conversations";
const NO_CONVERSATIONS = "Your chats with this app, and every run it makes on its own, land here.";

const LAST_TIME = "Last time";
const LIVE_WORK = "Live work";
const RAISE = "Raise";

function Who({ people }: { people: Person[] }) {
  return (
    <span className="flex items-center gap-2xs">
      {people.map((person) => (
        <Avatar key={person.name} title={person.name} className="size-(--size-glyph)">
          <AvatarFallback className="text-small">{person.initials}</AvatarFallback>
        </Avatar>
      ))}
    </span>
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

function Brief({ meeting }: { meeting: Meeting }) {
  return (
    <article className="flex flex-col gap-lg border-t border-edge py-xl">
      <header className="flex items-baseline gap-lg">
        <span className="font-mono text-label text-ink-soft">{meeting.at}</span>
        <h3 className="m-0 flex-1 text-body font-medium">{meeting.title}</h3>
        <Who people={meeting.attendees} />
      </header>
      <Line label={LAST_TIME}>{meeting.lastTime}</Line>
      <Line label={LIVE_WORK}>
        <span className="flex flex-wrap gap-xs">
          {meeting.live.map((item) => (
            <span
              key={item.ref + item.what}
              className="rounded-control bg-fill px-sm py-2xs text-small"
            >
              <span className="font-mono">{item.ref}</span> {item.what}
            </span>
          ))}
        </span>
      </Line>
      <Line label={RAISE}>
        <ul className="m-0 flex list-disc flex-col gap-2xs pl-xl">
          {meeting.raise.map((point) => (
            <li key={point}>{point}</li>
          ))}
        </ul>
      </Line>
      <div className="flex gap-xs">
        <Button
          variant="outline"
          size="bar"
          onClick={() => compose("Brief me on " + meeting.title + " again, with what changed.")}
        >
          Re-brief
        </Button>
      </div>
    </article>
  );
}

function FollowUpRow({ item }: { item: FollowUp }) {
  return (
    <tr className="border-t border-edge">
      <td className="py-lg pr-lg align-top text-label">{item.commitment}</td>
      <td className="py-lg pr-lg align-top">
        <Who people={[item.owner]} />
      </td>
      <td className="py-lg pr-lg align-top font-mono text-small text-ink-soft">{item.due}</td>
      <td className="py-lg pr-lg align-top">
        <span
          className={
            item.state === "Late"
              ? "rounded-control bg-attention px-sm py-2xs text-small"
              : "text-small text-ink-soft"
          }
        >
          {item.state}
        </span>
      </td>
      <td className="py-lg align-top">
        <Button
          variant="outline"
          size="bar"
          onClick={() => compose("Chase " + item.owner.name + " on: " + item.commitment)}
        >
          Chase
        </Button>
      </td>
    </tr>
  );
}

/** The meetings screen: what is coming and what the app found for it, what the last meetings
 *  committed to, and what they decided.
 *
 *  It answers the questions a member has in the order they ask them — what happens next, what do I
 *  owe, what did we settle — and every row is a record the app produced rather than a statement
 *  about what the app could do. */
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
      <Section title={NEXT_MEETINGS}>
        <div className="flex flex-col">
          {MEETINGS.map((meeting) => (
            <Brief key={meeting.title} meeting={meeting} />
          ))}
        </div>
      </Section>
      <Section title={FOLLOW_UPS_TITLE}>
        <table className="w-full border-collapse text-left">
          <tbody>
            {FOLLOW_UPS.map((item) => (
              <FollowUpRow key={item.commitment} item={item} />
            ))}
          </tbody>
        </table>
      </Section>
      <Section title={NOTES_TITLE}>
        <div className="flex flex-col">
          {NOTES.map((note) => (
            <div key={note.decision} className="flex gap-lg border-t border-edge py-lg">
              <span className="min-w-0 flex-1 text-label">{note.decision}</span>
              <span className="shrink-0 text-small text-ink-soft">{note.meeting}</span>
              <span className="shrink-0 font-mono text-small text-ink-soft">{note.on}</span>
            </div>
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
    tab="meetings"
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
