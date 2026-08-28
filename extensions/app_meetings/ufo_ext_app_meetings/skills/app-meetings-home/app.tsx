// The meetings app's page: a static site built with the portal's app kit. Edit this file and
// redeploy to change the page.
//
// It ships filled in. Every row below is placeholder written to read exactly like the real thing —
// real names, real times, real amounts — because this file is the shape the app rebuilds against
// its own sources on the first Build app. A page of empty bands would tell the app nothing about
// what to draw, and a member nothing about what the app is for.

import {
  AppConversations,
  AvatarStack,
  Badge,
  Button,
  Card,
  Fragment,
  Header,
  IconX,
  ObjectDetail,
  PanelEmpty,
  Section,
  SectionApp,
  Separator,
  cn,
  compose,
  mountApp,
  objectAt,
  slotOf,
  usePageHead,
  useState,
} from "ufo/kit";
import type { Placement } from "ufo/kit";

const APP = "Meetings";

/** What the app is for, in the one sentence a member reads before anything else on the page. */
const PURPOSE =
  "Briefs you before each meeting, and can turn what a meeting agreed into tracked follow-ups "
  + "and workspace notes.";

/** The line under the title: when the app last ran, and the counts a member scans for. It is the
 *  page's own status, so it says what happened rather than what the app could do. */
const STANDING = "Last brief 08:05 · 3 meetings ahead · 5 follow-ups open · 2 late";

/** The one feature the page offers rather than reports: follow-ups wait for the ask, so the band
 *  above the meetings states what turning them on gives and carries the ask that does it. A member
 *  who does not want them closes it. */
const OFFER = "Follow-ups after each meeting";
const OFFER_NOTE =
  "Turning this on writes down what each meeting committed to, with an owner and a date, and "
  + "chases what is late.";
const OFFER_ASK = "Turn on follow-ups.";
const TURN_ON = "Turn on";
const DISMISS = "Dismiss";

type Person = { name: string; email: string };

type Meeting = {
  day: string;
  when: string;
  title: string;
  attendees: Person[];
};

/** The next meetings: when, who is coming, and the one act. The brief itself — what last time
 *  settled, what work is open, what to raise — is what Prep fetches into chat, so a row stays one
 *  line and the card stays a schedule a member scans rather than reads. */
const MEETINGS: Meeting[] = [
  {
    day: "27",
    when: "Today · 3:30p",
    title: "Investor update review",
    attendees: [{ name: "Tomas Ferrer", email: "tomas@metalcraft.test" }],
  },
  {
    day: "28",
    when: "Thursday, 28 Aug · 11:00a",
    title: "Product design sync",
    attendees: [{ name: "Tomas Ferrer", email: "tomas@metalcraft.test" }, { name: "Ines Okafor", email: "ines@metalcraft.test" }],
  },
  {
    day: "1",
    when: "Monday, 1 Sept · 2:30p",
    title: "Northstar renewal",
    attendees: [{ name: "Rae Whitlock", email: "rae@metalcraft.test" }, { name: "Cleo Marsh", email: "cleo@metalcraft.test" }],
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
    owner: { name: "Rae Whitlock", email: "rae@metalcraft.test" },
    due: "22 Aug",
    state: "Late",
    meeting: "Northstar renewal",
  },
  {
    commitment: "Write the first-run copy for the one-button screen",
    owner: { name: "Ines Okafor", email: "ines@metalcraft.test" },
    due: "26 Aug",
    state: "Open",
    meeting: "Product design sync",
  },
  {
    commitment: "Confirm the waitlist number for the investor update",
    owner: { name: "Tomas Ferrer", email: "tomas@metalcraft.test" },
    due: "25 Aug",
    state: "Open",
    meeting: "Investor update review",
  },
  {
    commitment: "Decide whether built-in apps can be forked at launch",
    owner: { name: "Tomas Ferrer", email: "tomas@metalcraft.test" },
    due: "21 Aug",
    state: "Late",
    meeting: "Product design sync",
  },
  {
    commitment: "Share the renewal terms doc with Cleo",
    owner: { name: "Cleo Marsh", email: "cleo@metalcraft.test" },
    due: "27 Aug",
    state: "Open",
    meeting: "Northstar renewal",
  },
];

type Note = { decision: string; meeting: string };

type Decided = { day: string; on: string; notes: Note[] };

/** Decisions written into the workspace record, so the wiki and every later turn read them. They
 *  stand under the day the meeting reached them, because a decision is read by when it was made. */
const NOTES: Decided[] = [
  {
    day: "24",
    on: "24 Aug",
    notes: [
      {
        decision: "Built-in apps are not editable at launch; customising one means forking it.",
        meeting: "Product design sync",
      },
      {
        decision: "The first run offers one button, not a wizard.",
        meeting: "Product design sync",
      },
    ],
  },
  {
    day: "18",
    on: "18 Aug",
    notes: [
      {
        decision: "Northstar renewal is annual billing at $48,000.",
        meeting: "Northstar renewal",
      },
    ],
  },
];

const NEXT_MEETINGS = "Next meetings";
const FOLLOW_UPS_TITLE = "Follow-ups";
const NOTES_TITLE = "Notes";
const CONVERSATIONS = "Conversations";
const NO_CONVERSATIONS = "Your chats with this app, and every run it makes on its own, land here.";

/** The date a row is about, drawn as the tile the row leads with: the same square the controls on
 *  that row are tall, so the day, the faces and the act sit on one line. */
const DAY_TILE = cn(
  "flex size-(--size-control) shrink-0 items-center justify-center rounded-avatar",
  "bg-fill text-label font-medium text-ink-quiet",
);

/** What a row outside a card leads with instead of a tile: one hairline standing the height of the
 *  row, so a list of records reads down one left edge. */
const LEAD = "w-(--spacing-hair) shrink-0 self-stretch rounded-row bg-fill-strong";

/** The name of the record and the line under it. It holds a measure of its own, so the acts at the
 *  row's far edge wrap under it on a phone rather than squeezing the name to nothing. */
const NAME = "flex min-w-(--container-control-row) flex-1 flex-col gap-sm";

const ROW = "flex flex-wrap items-center gap-2xl py-2xl";

function Brief({ meeting, ruled }: { meeting: Meeting; ruled: boolean }) {
  return (
    <div className={cn(ROW, ruled && "border-t border-edge")}>
      <span className={DAY_TILE}>{meeting.day}</span>
      <div className={NAME}>
        <h3 className="m-0 truncate text-label font-medium tracking-ui">{meeting.title}</h3>
        <p className="m-0 truncate text-label text-ink-quiet">{meeting.when}</p>
      </div>
      <AvatarStack people={meeting.attendees} />
      <Button
        variant="outline"
        size="bar"
        onClick={() => compose("Prep me for " + meeting.title + ".")}
      >
        Prep
      </Button>
    </div>
  );
}

function FollowUpRow({ item, ruled }: { item: FollowUp; ruled: boolean }) {
  return (
    <div className={cn(ROW, ruled && "border-t border-edge")}>
      <span aria-hidden className={LEAD} />
      <div className={NAME}>
        <span className="truncate text-label font-medium tracking-ui">{item.commitment}</span>
        <span className="truncate text-label text-ink-quiet">
          {item.meeting} · {item.due}
        </span>
      </div>
      <AvatarStack people={[item.owner]} />
      <Badge tone={item.state === "Late" ? "attention" : "default"}>{item.state}</Badge>
      <Button
        variant="outline"
        size="bar"
        onClick={() => compose("Chase " + item.owner.name + " on: " + item.commitment)}
      >
        Chase
      </Button>
    </div>
  );
}

function NoteRow({ note, on, ruled }: { note: Note; on: string; ruled: boolean }) {
  return (
    <div className={cn(ROW, ruled && "border-t border-edge")}>
      <span aria-hidden className={LEAD} />
      <div className={NAME}>
        <span className="truncate text-label font-medium tracking-ui">{note.decision}</span>
        <span className="truncate text-label text-ink-quiet">{note.meeting}</span>
      </div>
      <Button
        variant="outline"
        size="bar"
        onClick={() => compose("Summarise " + note.meeting + " on " + on + ".")}
      >
        Summary
      </Button>
    </div>
  );
}

/** One day of decisions: the date it was, the day's own act, and a row per decision under it. */
function Day({ decided }: { decided: Decided }) {
  return (
    <div className="flex flex-col">
      <div className="flex flex-wrap items-center gap-2xl py-sm">
        <span className={DAY_TILE}>{decided.day}</span>
        <h3 className="m-0 min-w-0 flex-1 truncate text-body font-medium tracking-ui">
          {decided.on}
        </h3>
        <Button
          variant="outline"
          size="bar"
          onClick={() => compose("What did we decide on " + decided.on + "?")}
        >
          Ask
        </Button>
      </div>
      {decided.notes.map((note, index) => (
        <NoteRow key={note.decision} note={note} on={decided.on} ruled={index > 0} />
      ))}
    </div>
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
  const [offered, setOffered] = useState(true);
  const band = usePageHead(
    <Header pinned heading={1} title={APP} lede={PURPOSE} note={STANDING} />,
  );
  return (
    <>
      {band}
      {offered ? (
        <div className="flex flex-wrap items-center gap-2xl">
          <div className={NAME}>
            <p className="m-0 text-label font-medium">{OFFER}</p>
            <p className="m-0 text-label text-ink-quiet">{OFFER_NOTE}</p>
          </div>
          <span className="flex shrink-0 items-center gap-sm">
            <Button variant="outline" size="bar" onClick={() => compose(OFFER_ASK)}>
              {TURN_ON}
            </Button>
            <Button variant="quiet" size="icon" aria-label={DISMISS} onClick={() => setOffered(false)}>
              <IconX aria-hidden />
            </Button>
          </span>
        </div>
      ) : null}
      <Section title={NEXT_MEETINGS}>
        <Card rows>
          {MEETINGS.map((meeting, index) => (
            <Brief key={meeting.title} meeting={meeting} ruled={index > 0} />
          ))}
        </Card>
      </Section>
      <Section title={FOLLOW_UPS_TITLE}>
        <div className="flex flex-col">
          {FOLLOW_UPS.map((item, index) => (
            <FollowUpRow key={item.commitment} item={item} ruled={index > 0} />
          ))}
        </div>
      </Section>
      <Section title={NOTES_TITLE}>
        <div className="flex flex-col gap-2xl">
          {NOTES.map((decided, index) => (
            <Fragment key={decided.on}>
              {index > 0 ? <Separator /> : null}
              <Day decided={decided} />
            </Fragment>
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
