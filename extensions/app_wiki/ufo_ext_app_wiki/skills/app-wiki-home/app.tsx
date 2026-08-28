// The wiki app's page: a static site built with the portal's app kit. Edit this file and redeploy
// to change the page.

import {
  BANDS,
  Button,
  Dialog,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  Facts,
  Group,
  Header,
  IconChevronDown,
  IconChevronUp,
  IconDots,
  Moment,
  ObjectDetail,
  Panel,
  PanelBlank,
  RebuildDialog,
  RowLines,
  SHARED_SUBJECT,
  SectionApp,
  appended,
  beside,
  closed,
  cn,
  day,
  isMemberAudience,
  mountApp,
  objectAt,
  opened,
  slotOf,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMainAgent,
  usePageHead,
  usePanelRead,
  useRef,
  useState,
  useViewer,
} from "ufo/kit";
import type {
  Crumb,
  Member,
  ObjectAddress,
  Placement,
  ReactNode,
  ReactMouseEvent,
} from "ufo/kit";

/** What the store calls the page's opening paragraph: one item per subject, rewritten in place by
 *  the job that re-reads every fact standing under it. It is written to be read whole, so it is
 *  what a page opens on. */
const SUMMARY_CLASS = "overview";

const NOT_FOUND = 404;
/** What an item the store filed on its own is classed as, before any consolidation. */
const ATOM_CLASS = "fact";
/** What the store calls a cluster of aged rows the consolidator collapsed into one. The rows it
 *  stands for are stamped superseded and leave every band the moment it is written, so a band
 *  drawing atoms alone loses what a member recorded instead of showing what replaced it. Every
 *  summary carries the `fact` kind, so the Facts band is where they stand. */
const CLUSTER_CLASS = "semantic";
const MEMBER_PREFIX = "member/";
/** The app's own name, which is what its front page is called and what every page and sheet inside
 *  it is reached from. */
const WIKI = "Wiki";
/** What a member's page is headed by until the roster has answered who they are. */
const MEMBER = "Member";
const MEMORY_KIND = "memory";
const MEMBER_KIND = "member";
/** Memory items are named by uuid, so the index's default order by name is arbitrary. `written` is
 *  the field the kind declares for the moment it recorded an item, and every band reads newest
 *  first — the order a document states what it knows in. */
const NEWEST_FIRST = "order_by=written&order=desc";
/** The overview's read: the written paragraph, which carries `item_class=overview` — the class
 *  axis, not a memory kind — newest first. A deploy that runs without memory registers no `memory`
 *  kind at all and answers the listing with a 404, which the band states as memory being absent
 *  rather than as a fault. */
/** One band's read of one class. The listing filter takes a single value and the portal cuts its
 *  answer to a page before this app sees it, so asking for every class and sorting them here would
 *  spend that page on rows the band does not draw — a band whose newest page is all `episodic`
 *  would draw nothing and report itself absent. Two reads, each filtered where the cut happens. */
const bandRead = (memoryKind: string, itemClass: string) =>
  "/objects/" +
  MEMORY_KIND +
  "?memory_kind=" +
  memoryKind +
  "&item_class=" +
  itemClass +
  "&" +
  NEWEST_FIRST;

const OVERVIEW_READ =
  "/objects/" + MEMORY_KIND + "?item_class=" + SUMMARY_CLASS + "&" + NEWEST_FIRST;
/** What a section's own paragraph is classed as: one row per topic, rewritten in place by the job
 *  that re-reads the topic rather than appended to. One read answers every band, because five reads
 *  for five paragraphs is five round trips for one document. */
const SECTION_CLASS = "section";
const SECTION_READ =
  "/objects/" + MEMORY_KIND + "?item_class=" + SECTION_CLASS + "&" + NEWEST_FIRST;
/** How many rows a topic holds behind its fold before the Memory tab is the better place to
 *  keep reading. */
const TOPIC_ROWS = 24;

const MEMORY_ABSENT =
  "This deploy runs without memory, so there is nothing to read here.";
const PRIVATE_TO_THEM =
  "What the apps remember about a colleague is theirs to read. This page states their standing in " +
  "the workspace and nothing else.";

type TopicSpec = { memoryKind: string; title: string; note: string };

/** The parts each page is divided into: the memory kinds the store already files a write under,
 *  each read as the section of a wiki it answers. Nothing here is derived — a kind is a column on
 *  the item, so a section names what somebody already told an app. The two sets differ only in
 *  their words, because a workspace's own knowledge and one person's are read differently even
 *  where the kind filing them is the same. */
const WORKSPACE_TOPICS: TopicSpec[] = [
  {
    memoryKind: "preference",
    title: "How the team works",
    note: "How this workspace has said it wants things done.",
  },
  {
    memoryKind: "decision",
    title: "Decisions",
    note: "What was settled, and stands until someone corrects it.",
  },
  {
    memoryKind: "task",
    title: "Open work",
    note: "What the workspace asked to have carried.",
  },
  {
    memoryKind: "event",
    title: "History",
    note: "What happened, newest first.",
  },
  {
    memoryKind: "fact",
    title: "Facts",
    note: "What is known about this workspace.",
  },
];

const MEMBER_TOPICS: TopicSpec[] = [
  {
    memoryKind: "preference",
    title: "How you work",
    note: "How you have said you want things done.",
  },
  {
    memoryKind: "decision",
    title: "Decisions",
    note: "What was settled, and stands until you correct it.",
  },
  {
    memoryKind: "task",
    title: "Tasks",
    note: "What you asked to have carried.",
  },
  {
    memoryKind: "event",
    title: "History",
    note: "What happened, newest first.",
  },
  { memoryKind: "fact", title: "Facts", note: "What is known about you." },
];

type MemoryObject = {
  name: string;
  summary: string;
  text?: string;
  subject: string;
  item_class: string;
  memory_kind: string;
  written: string | null;
  agent_id: string;
  created_from_page_id: string | null;
  created_from_page_title: string | null;
  created_from_page_stream: string | null;
};

type ObjectsPayload = { objects: MemoryObject[] };

type MemberRow = {
  name: string;
  email: string;
  admin: boolean;
  seated: boolean;
};

type MembersPayload = { objects: MemberRow[] };

/** What the nightly people pass writes about one member: the part they play here and what they are
 *  carrying now, both read from the workspace's shared facts alone. A member the pass has not
 *  reached yet simply has none, and the roster row stands as it always did. */
type ProfileRow = {
  name: string;
  role: string | null;
  focus: string | null;
};

type ProfilesPayload = { objects: ProfileRow[] };

const PROFILE_KIND = "profile";

/** Every member's role and focus in one read, keyed by the member id the roster names them by. A
 *  deploy running without memory registers no `profile` kind and answers 404, which leaves the
 *  roster drawn exactly as it is rather than failing the band around it. */
function useProfiles(reloads: number): Map<string, ProfileRow> {
  const state = usePanelRead<ProfilesPayload>(
    "/objects/" + PROFILE_KIND + "?order_by=written&order=desc",
    reloads,
  );
  return new Map(
    state.phase === "ready"
      ? distinct(state.payload.objects).map((row) => [row.name, row])
      : [],
  );
}

/** Every topic's paragraph in one read. A band takes its own out of the answer, so the page pays one
 *  round trip for the five it draws and a topic the job has not written yet simply has none. */
function useSections(reloads: number, scope: Scope): MemoryObject[] {
  const state = usePanelRead<ObjectsPayload>(SECTION_READ, reloads);
  return state.phase === "ready"
    ? inScope(distinct(state.payload.objects), scope)
    : [];
}

function useRoster(reloads: number) {
  const main = useMainAgent();
  return usePanelRead<MembersPayload>(
    main === null ? null : "/objects/" + MEMBER_KIND + "?agent=" + main.id,
    reloads,
  );
}

/** Which records a page is built from. The read answers the viewer's own subjects and no one
 *  else's, so the workspace's own knowledge and the viewer's private knowledge arrive together and
 *  are told apart by their subject alone. */
type Scope = "shared" | "own";

function inScope<T extends { subject: string | null }>(
  rows: T[],
  scope: Scope,
): T[] {
  return rows.filter((row) =>
    row.subject === null
      ? false
      : scope === "shared"
        ? row.subject === SHARED_SUBJECT
        : isMemberAudience(row.subject),
  );
}

/** One record reaches a page once per agent the viewer reads through: the index fans out over
 *  every agent their web audience holds, and a memory or a member is scoped by subject or by
 *  workspace rather than by an agent, so each agent answers the same row. The uuid naming it is
 *  the identity, and the fan-out still earns its keep — a page-derived item only one agent's
 *  source grants admit arrives once, from that agent. */
function distinct<Row extends { name: string }>(rows: Row[]): Row[] {
  const seen = new Set<string>();
  const kept: Row[] = [];
  for (const row of rows) {
    if (seen.has(row.name)) continue;
    seen.add(row.name);
    kept.push(row);
  }
  return kept;
}

function memberAt(opens: string[]): string | null {
  const held = opens.find((id) => id.startsWith(MEMBER_PREFIX));
  return held ? held.slice(MEMBER_PREFIX.length) || null : null;
}

function Wiki({
  crumb,
  place,
  onPlace,
}: {
  crumb?: Crumb;
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const viewer = useViewer();
  const opens = place.opens ?? [];
  const member = memberAt(opens);

  return (
    <>
      <div className={BANDS}>
        {member === null ? (
          <Workspace viewer={viewer} opens={opens} onPlace={onPlace} />
        ) : (
          <Member
            id={member}
            viewer={viewer}
            crumb={crumb}
            opens={opens}
            onPlace={onPlace}
          />
        )}
      </div>

      {opens.slice(-1).map((id) => {
        const held = objectAt(id);
        if (held === null) return null;
        return (
          <RecordSlot
            key={id}
            id={id}
            held={held}
            opens={opens}
            onPlace={onPlace}
          />
        );
      })}
    </>
  );
}

function RecordSlot({
  id,
  held,
  opens,
  onPlace,
}: {
  id: string;
  held: ObjectAddress;
  opens: string[];
  onPlace: (place: Placement) => void;
}) {
  const shut = () => onPlace({ opens: closed(opens, id) });
  return (
    <ObjectDetail
      agentId={held.agent}
      kind={held.kind}
      name={held.name}
      onOpen={(next) => onPlace({ opens: opened(opens, slotOf(next), id) })}
      onBack={shut}
    />
  );
}

type Entry = { id: string; title: string };

/** A wiki page: the article at the page's own left edge, and its contents in a column beside it on
 *  the right. The contents are what a member navigates a long page by, so they stand where an
 *  encyclopedia puts them — outside the article, in view while it scrolls — and they list only the
 *  parts this page actually drew. They stand after the article in the document as well as to its
 *  right, so what is read, tabbed and drawn are one order: the article first, its index after it.
 *  They carry no heading of their own: a column of the page's own section names, set back from
 *  them, is already read as the way through it, and the landmark names it for a reader who cannot
 *  see that. Below the narrow breakpoint the column is dropped rather than stacked: a contents list
 *  above the article it indexes is a second thing to scroll past to reach the first. */
function Article({
  header,
  entries,
  children,
}: {
  header: ReactNode;
  entries: Entry[];
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-6xl">
      {header}
      <div className="flex gap-7xl">
        <div className={cn("min-w-0 max-w-section flex-1", BANDS)}>
          {children}
        </div>
        <nav
          aria-label="Contents"
          className="sticky top-0 h-fit w-(--container-sidebar) shrink-0 max-narrow:hidden"
        >
          <ul className="m-0 flex list-none flex-col gap-2xs p-0">
            {entries.map((entry) => (
              <li key={entry.id}>
                <button
                  type="button"
                  className="block w-full cursor-pointer border-0 bg-transparent p-0 text-start text-label leading-chrome text-ink-soft hover:underline"
                  onClick={() =>
                    document
                      .getElementById(entry.id)
                      ?.scrollIntoView({ behavior: "smooth", block: "start" })
                  }
                >
                  {entry.title}
                </button>
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </div>
  );
}

/** Which parts a page drew, and how current each one is. A part decides its own emptiness from its
 *  own read, so the contents cannot be listed until each has answered; each reports itself, and the
 *  page's own stamp is the newest moment any part carries. */
function usePresence() {
  const [seen, setSeen] = useState<
    Record<string, { drawn: boolean; newest: string | null }>
  >({});
  const report = useCallback(
    (id: string, drawn: boolean, newest: string | null) => {
      setSeen((held) => {
        const was = held[id];
        if (was && was.drawn === drawn && was.newest === newest) return held;
        return { ...held, [id]: { drawn, newest } };
      });
    },
    [],
  );
  const present: Record<string, boolean> = {};
  for (const [id, part] of Object.entries(seen)) present[id] = part.drawn;
  const stamps = Object.values(seen)
    .map((part) => part.newest)
    .filter((at): at is string => at !== null)
    .sort();
  return { present, report, updated: stamps.at(-1) ?? null };
}

/** The one control the page's own acts hang off, and the stamp saying how current it is.
 *
 *  The two acts are not the same act. Reload re-reads every part: a topic is a live projection of
 *  `memory_item`, so a fresh read is the whole of what a portal read can do on its own. Rebuilding
 *  the page facts asks the derivation pass to write those rows again from the pages they came from,
 *  and it reaches nothing else — so the act is named for the band it reaches and the dialog states
 *  the two bands it leaves alone. A control named for the page while it redoes one part of it reads
 *  as a promise, and the rows it would quietly skip are the ones nothing can write a second time. */
function Acts({
  updated,
  onReload,
}: {
  updated: string | null;
  onReload: () => void;
}) {
  const [rebuilding, setRebuilding] = useState(false);
  return (
    <div className="flex items-center gap-md">
      {updated === null ? null : (
        <span className="text-label leading-chrome text-ink-soft">
          Updated <Moment at={updated} />
        </span>
      )}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="row" size="icon" aria-label="Page actions">
            <IconDots aria-hidden />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onSelect={onReload}>Reload</DropdownMenuItem>
          <DropdownMenuItem onSelect={() => setRebuilding(true)}>
            Rebuild page facts
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <Dialog open={rebuilding} onOpenChange={setRebuilding}>
        {rebuilding ? (
          <RebuildDialog title="Rebuild Page Facts" kind="page">
            <p className="m-0">
              Rows derived from synced pages are written again from those pages, as the derivation
              pass reaches each one. No row is dropped before its replacement is written. Rows
              drawn from the pages a tool writes about its own runs are removed, because the wiki
              writes no rows from those pages now.
            </p>
            <p className="m-0">
              The paragraphs are not rebuilt. The nightly passes write them again from the rows that
              stand under them once this derivation has run.
            </p>
            <p className="m-0">
              Rows an app recorded in a conversation are not rebuilt. They came from conversations
              that have ended, and nothing can write them a second time.
            </p>
          </RebuildDialog>
        ) : null}
      </Dialog>
    </div>
  );
}

/** What the workspace knows about itself, and who is in it. This is the app's own front page, so
 *  it states the shape of the place first — how many people, how many hold a seat — then what has
 *  been settled here, and last the roster, which is the way through to a person. */
function Workspace({
  viewer,
  opens,
  onPlace,
}: {
  viewer: string | null;
  opens: string[];
  onPlace: (place: Placement) => void;
}) {
  const [reloads, setReloads] = useState(0);
  const { present, report, updated } = usePresence();
  const summary = usePanelRead<ObjectsPayload>(OVERVIEW_READ, reloads);
  const sections = useSections(reloads, "shared");
  const profiles = useProfiles(reloads);
  const members = useRoster(reloads);
  const roster =
    members.phase === "ready" ? distinct(members.payload.objects) : [];
  const entries: Entry[] = [
    ...(present.overview ? [{ id: "overview", title: "Overview" }] : []),
    { id: "people", title: "People" },
    ...WORKSPACE_TOPICS.filter((topic) => present[topic.memoryKind]).map(
      (topic) => ({
        id: topic.memoryKind,
        title: topic.title,
      }),
    ),
    { id: "details", title: "Details" },
  ];

  const band = usePageHead(
    <Header
      pinned
      heading={1}
      title={WIKI}
      acts={<Acts updated={updated} onReload={() => setReloads((run) => run + 1)} />}
    />,
  );

  return (
    <Article
      entries={entries}
      header={
        <>
          {band}
          {viewer === null ? null : (
            <p className="m-0 text-label leading-chrome">{viewer}</p>
          )}
        </>
      }
    >
      <Overview state={summary} scope="shared" onPresent={report} />

      <People
        state={members}
        rows={roster}
        profiles={profiles}
        viewer={viewer}
        onOpen={(id) => onPlace({ opens: opened(opens, MEMBER_PREFIX + id) })}
      />

      {WORKSPACE_TOPICS.map((topic) => (
        <Topic
          key={topic.memoryKind}
          topic={topic}
          scope="shared"
          sections={sections}
          reloads={reloads}
          opens={opens}
          onPresent={report}
          onPlace={onPlace}
        />
      ))}

      <section id="details" className="scroll-mt-lg">
        <Group title="Details">
          <Facts
            rows={[
              { label: "Members", value: String(roster.length) },
              {
                label: "Seated",
                value: String(roster.filter((row) => row.seated).length),
              },
              {
                label: "Admins",
                value: String(roster.filter((row) => row.admin).length),
              },
            ]}
          />
        </Group>
      </section>
    </Article>
  );
}

/** The roster as the way into a person's page. It is drawn even when it holds only the viewer,
 *  because a workspace with one member is a fact about the workspace rather than an empty topic —
 *  and unlike a topic, the member reading it is always in it. */
function People({
  state,
  rows,
  profiles,
  viewer,
  onOpen,
}: {
  state: ReturnType<typeof usePanelRead<MembersPayload>>;
  rows: MemberRow[];
  /** What each member does and carries, by member id. Absent for a member the pass has not read. */
  profiles: Map<string, ProfileRow>;
  viewer: string | null;
  onOpen: (id: string) => void;
}) {
  return (
    <Band id="people" title="People" note="Everyone in this workspace.">
      <Panel state={state}>
        {() => (
          <RowLines
            rows={rows}
            rowKey={(row) => row.name}
            primary={(row) =>
              row.email === viewer ? row.email + " (you)" : row.email
            }
            meta={(row) => {
              const profile = profiles.get(row.name);
              return [
                profile?.role ?? null,
                row.admin ? "Admin" : "Member",
                row.seated ? "Seated" : "No seat",
                profile?.focus ?? null,
              ];
            }}
            whole
            open={(row) => () => onOpen(row.name)}
          />
        )}
      </Panel>
    </Band>
  );
}

/** One member's page. Their own memory is theirs alone — the store answers a portal read on the
 *  reader's subjects and no one else's, an admin included — so this page is the whole of what the
 *  workspace can say about a colleague, and says so rather than drawing empty topics that would
 *  read as a person nothing is known about. */
function Member({
  id,
  viewer,
  crumb,
  opens,
  onPlace,
}: {
  id: string;
  viewer: string | null;
  crumb?: Crumb;
  opens: string[];
  onPlace: (place: Placement) => void;
}) {
  const [reloads, setReloads] = useState(0);
  const { present, report, updated } = usePresence();
  const members = useRoster(reloads);
  const sections = useSections(reloads, "own");
  const found =
    members.phase === "ready"
      ? (distinct(members.payload.objects).find((row) => row.name === id) ??
        null)
      : null;
  const mine = found !== null && found.email === viewer;
  const entries: Entry[] = [
    ...(mine
      ? MEMBER_TOPICS.filter((topic) => present[topic.memoryKind]).map(
          (topic) => ({
            id: topic.memoryKind,
            title: topic.title,
          }),
        )
      : []),
    { id: "details", title: "Details" },
  ];

  const band = usePageHead(
    <Header
      pinned
      heading={1}
      crumb={crumb}
      title={found?.email ?? MEMBER}
      acts={<Acts updated={updated} onReload={() => setReloads((run) => run + 1)} />}
      closes={found?.email ?? MEMBER}
      onClose={() => onPlace({ opens: closed(opens, MEMBER_PREFIX + id) })}
    />,
  );

  return (
    <Article entries={entries} header={band}>
      {found === null ? (
        <Panel state={members}>
          {() => (
            <PanelBlank body="No member answers that address in this workspace." />
          )}
        </Panel>
      ) : mine ? (
        <>
          {MEMBER_TOPICS.map((topic) => (
            <Topic
              key={topic.memoryKind}
              topic={topic}
              scope="own"
              sections={sections}
              reloads={reloads}
              opens={opens}
              from={MEMBER_PREFIX + id}
              onPresent={report}
              onPlace={onPlace}
            />
          ))}
        </>
      ) : (
        <Band title="Memory" note="What the apps remember about this person.">
          <p>{PRIVATE_TO_THEM}</p>
        </Band>
      )}

      {found === null ? null : (
        <section id="details" className="scroll-mt-lg">
          <Group title="Details">
            <Facts
              rows={[
                { label: "Role", value: found.admin ? "Admin" : "Member" },
                { label: "Seat", value: found.seated ? "Seated" : "No seat" },
                {
                  label: "Audience",
                  value: mine ? "Only you" : "Private to them",
                },
              ]}
            />
          </Group>
        </section>
      )}
    </Article>
  );
}

/** The account of whoever the page is about, when there is one. Nothing is drawn until the
 *  consolidator has written a summary — an empty overview is the page apologising for itself, and
 *  the topics below already say what is known. A deploy without memory says so, because that is a
 *  fact about the deploy rather than an empty topic. */
function Overview({
  state,
  scope,
  onPresent,
}: {
  state: ReturnType<typeof usePanelRead<ObjectsPayload>>;
  scope: Scope;
  onPresent: (id: string, drawn: boolean, newest: string | null) => void;
}) {
  const matches =
    state.phase === "ready" ? inScope(distinct(state.payload.objects), scope) : [];
  const absent = state.phase === "failed" && state.status === NOT_FOUND;
  const drawn = matches.length > 0 || state.phase === "failed";
  const newest =
    matches
      .map((row) => row.written)
      .filter((at): at is string => at !== null)
      .sort()
      .at(-1) ?? null;
  useEffect(() => {
    onPresent("overview", drawn, newest);
  }, [onPresent, drawn, newest]);
  if (state.phase === "loading") return null;
  if (absent) {
    return (
      <Band id="overview" title="Overview" note={overviewNote(state, scope)}>
        <PanelBlank body={MEMORY_ABSENT} />
      </Band>
    );
  }
  if (state.phase === "failed") {
    return (
      <Band id="overview" title="Overview" note={overviewNote(state, scope)}>
        <Panel state={state}>{() => null}</Panel>
      </Band>
    );
  }
  if (matches.length === 0) return null;
  const written = matches[0];
  if (written === undefined) return null;
  return (
    <Band id="overview" title="Overview" note={overviewNote(state, scope)}>
      <p>{written.text ?? written.summary}</p>
    </Band>
  );
}

/** How current the account is, stamped once over the whole of it: what a reader is deciding is
 *  whether to trust this page at all, which the oldest line on it cannot answer. */
function overviewNote(
  state: ReturnType<typeof usePanelRead<ObjectsPayload>>,
  scope: Scope,
): string {
  const written =
    scope === "shared"
      ? "Written from every fact this workspace holds."
      : "Written from every fact recorded about you.";
  if (state.phase !== "ready") return written;
  const stamps = inScope(distinct(state.payload.objects), scope)
    .map((row) => row.written)
    .filter((at): at is string => at !== null)
    .sort();
  const last = stamps.at(-1);
  return last === undefined
    ? written
    : written + " Last written " + day(last) + ".";
}

/** One topic's band: the memory kind's own listing, set as a bulleted list. A row opens the item
 *  it came from — the provenance a summary of it cannot carry — so each bullet is the control that
 *  opens it, underlining under the pointer rather than standing out of the prose as a link the
 *  member has to read past on every line. The bullet whose record is standing carries the mark
 *  every open row in the portal takes, so the page states where the record beside it was opened
 *  from and a second bullet reads as a step taken rather than as the first record vanishing.
 *
 *  A topic holding nothing is not drawn: a wiki states what is known, and a heading over the words
 *  "nothing recorded" is a row of furniture the member cannot act on. A read that *failed* is
 *  drawn, and says so — silence there would be the page reporting an empty topic it never read. */
function Topic({
  topic,
  scope,
  sections,
  reloads,
  opens,
  from,
  onPresent,
  onPlace,
}: {
  topic: TopicSpec;
  scope: Scope;
  /** Every topic's paragraph, read once for the page and handed to the band it belongs to. */
  sections: MemoryObject[];
  reloads: number;
  opens: string[];
  /** The page a bullet is pressed on, which every record it opens stands after. */
  from?: string;
  onPresent: (id: string, drawn: boolean, newest: string | null) => void;
  onPlace: (place: Placement) => void;
}) {
  const atoms = usePanelRead<ObjectsPayload>(bandRead(topic.memoryKind, ATOM_CLASS), reloads);
  const clusters = usePanelRead<ObjectsPayload>(
    bandRead(topic.memoryKind, CLUSTER_CLASS),
    reloads,
  );
  const state = atoms.phase === "ready" ? clusters : atoms;
  const rows =
    atoms.phase === "ready" && clusters.phase === "ready"
      ? inScope(distinct([...atoms.payload.objects, ...clusters.payload.objects]), scope)
          .sort((left, right) => ((left.written ?? "") < (right.written ?? "") ? 1 : -1))
          .slice(0, TOPIC_ROWS)
      : [];
  const drawn = rows.length > 0;
  const newest = rows[0]?.written ?? null;
  useEffect(() => {
    onPresent(topic.memoryKind, drawn, newest);
  }, [onPresent, topic.memoryKind, drawn, newest]);
  if (state.phase === "loading") return null;
  if (state.phase === "failed") {
    return (
      <Band id={topic.memoryKind} title={topic.title} note={topic.note}>
        <Panel state={state}>{() => null}</Panel>
      </Band>
    );
  }
  if (!drawn) return null;
  const written = sections.find((row) => row.memory_kind === topic.memoryKind);
  return (
    <Band id={topic.memoryKind} title={topic.title} note={topic.note}>
      {written === undefined ? null : <p>{written.text ?? written.summary}</p>}
      <ul>
        {rows.map((row) => {
          const id = slotOf({ agent: row.agent_id, kind: MEMORY_KIND, name: row.name });
          const standing = opens.includes(id);
          const press = (event: ReactMouseEvent<HTMLButtonElement>) =>
            onPlace({ opens: beside(event) ? appended(opens, id) : opened(opens, id, from) });
          return (
            <li key={row.name}>
              <button
                type="button"
                aria-current={standing}
                onClick={press}
                onAuxClick={(event) => {
                  if (beside(event)) press(event);
                }}
                className={cn(
                  "block w-full cursor-pointer border-0 p-0 text-start text-inherit hover:underline",
                  standing ? "rounded-control bg-fill" : "bg-transparent",
                )}
              >
                {row.summary}
                {row.created_from_page_title === null ? null : (
                  <span className="text-small text-ink-soft">
                    {" " + row.created_from_page_title}
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </Band>
  );
}

/** One part of the document: a heading ruled off from what it introduces, then the page's own
 *  prose. The body is `typeset` — the register every document in the portal is read in, so a
 *  paragraph, a bullet and a numbered step here are the same elements an agent's reply sets, and
 *  the page carries no list styling of its own.
 *
 *  A part is folded to about four lines and the heading is the control that opens it: a member
 *  scanning six headings for the one they came for should reach it without paging through the one
 *  above. The note stands outside the fold, because it says what the part is and a preview that
 *  spent a line on that would show one item less. The fold is offered only where something is
 *  actually behind it — a part that already fits is never given a chevron that would do nothing —
 *  which is why the height is measured rather than the rows counted: what overflows is a wrapped
 *  line, not an item. */
function Band({
  id,
  title,
  note,
  children,
}: {
  id?: string;
  title: string;
  note: string;
  children: ReactNode;
}) {
  const body = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [folded, setFolded] = useState(false);
  const region = useId();

  useLayoutEffect(() => {
    const box = body.current;
    if (!box || open) return;
    const measure = () => setFolded(box.scrollHeight > box.clientHeight);
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [open, children]);

  const heading = (
    <span className="flex w-full items-center gap-md text-body font-medium tracking-ui">
      <span className="flex-1 text-start">{title}</span>
      {folded ? (
        open ? (
          <IconChevronUp className="size-icon shrink-0" aria-hidden />
        ) : (
          <IconChevronDown className="size-icon shrink-0" aria-hidden />
        )
      ) : null}
    </span>
  );

  return (
    <section id={id} className="flex w-full flex-col scroll-mt-lg">
      <h2 className="m-0 border-b border-fill pb-lg">
        {folded ? (
          <button
            type="button"
            aria-expanded={open}
            aria-controls={region}
            onClick={() => setOpen((shown) => !shown)}
            className="flex w-full cursor-pointer border-0 bg-transparent p-0 text-inherit"
          >
            {heading}
          </button>
        ) : (
          heading
        )}
      </h2>
      <p className="m-0 mt-lg text-label leading-chrome">{note}</p>
      <div
        id={region}
        ref={body}
        className={cn(
          "typeset relative mt-lg overflow-hidden text-label leading-chrome",
          !open && "max-h-(--size-fold)",
        )}
      >
        {children}
        {folded && !open ? (
          <div
            aria-hidden
            className="pointer-events-none absolute inset-x-0 bottom-0 h-7xl bg-linear-to-t from-background"
          />
        ) : null}
      </div>
    </section>
  );
}

mountApp(document.getElementById("root")!, (init) => (
  <SectionApp
    tab="wiki"
    init={init}
    view={{
      label: "Wiki",
      remountOnPlace: false,
      ownsHeader: true,
      render: (place, onPlace) => (
        <Wiki crumb={init.crumb} place={place} onPlace={onPlace} />
      ),
    }}
  />
));
