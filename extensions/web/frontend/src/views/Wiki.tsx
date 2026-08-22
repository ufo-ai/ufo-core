import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type MouseEvent,
  type ReactNode,
} from "react";
import { IconChevronDown, IconChevronUp, IconDots } from "@tabler/icons-react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Facts, Group } from "@/components/ui/facts";
import { ObjectDetail, objectAt, slotOf, type ObjectAddress } from "@/kernel/objects";
import type { Placement } from "@/kernel/pager";
import { BANDS, Header, usePageHead } from "@/kernel/pane";
import { Panel, PanelBlank, usePanelRead } from "@/kernel/panel";
import { RebuildDialog } from "@/kernel/rebuild";
import { RowLines } from "@/kernel/rows";
import { appended, beside, closed, opened, useSlot } from "@/kernel/slots";
import { isMemberAudience, SHARED_SUBJECT, useViewer } from "@/lib/audience";
import { cn } from "@/lib/cn";
import { day, Moment } from "@/lib/moments";

/** What the store calls a consolidated summary: a cluster of facts the consolidator collapsed into
 *  one item. It is the only memory written to be read whole, so it is what a page opens on. */
const SUMMARY_CLASS = "semantic";
/** What an item the store filed on its own is classed as, before any consolidation. */
const ATOM_CLASS = "fact";
const MEMBER_PREFIX = "member/";
/** The app's own name, which is what its front page is called and what every page and lane inside
 *  it is reached from. */
const WIKI = "Wiki";
/** What a member's page is headed by until the roster has answered who they are. */
const MEMBER = "Member";
const MEMORY_KIND = "memory";
const MEMBER_KIND = "member";
/** Memory items are named by uuid, so the index's default order by name is arbitrary. `written` is
 *  the field the kind declares for the moment it recorded an item, and every band reads newest
 *  first — the order a document states what it knows in.
 *
 *  A topic lists the atomic items only. The consolidator files its summaries under the `fact` kind
 *  too, so without the class the Facts topic would restate, item by item, the Overview standing
 *  above it — and the reader would meet the same sentence twice on one page. */
const NEWEST_FIRST =
  "item_class=" + ATOM_CLASS + "&order_by=written&order=desc";
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

type Match = {
  text: string;
  kind: string;
  ref: string | null;
  created_at: string | null;
  subject: string | null;
};

type MemoryPayload = { available: boolean; matches: Match[] };

type MemoryObject = {
  name: string;
  summary: string;
  subject: string;
  memory_kind: string;
  written: string | null;
  agent_id: string;
};

type ObjectsPayload = { objects: MemoryObject[] };

type MemberRow = {
  name: string;
  email: string;
  admin: boolean;
  seated: boolean;
};

type MembersPayload = { objects: MemberRow[] };

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

/** The member the page stands on. A member's page is not a lane standing beside the workspace's —
 *  it is what the pane's own body draws — so it heads the track as the root every lane after it was
 *  opened from: a record opens after it and leaves it standing, and shutting it shuts them with it.
 *  Carried in the track rather than in a key of its own, so one link states the whole screen. */
function memberAt(opens: string[]): string | null {
  const held = opens.find((id) => id.startsWith(MEMBER_PREFIX));
  return held ? held.slice(MEMBER_PREFIX.length) || null : null;
}

/** The Wiki app: the workspace is what it opens on, and one member is a page inside it. Both are
 *  the same document — a heading, what is known under it, and the people it is known about — so
 *  they are one route and one placement rather than two screens that would drift apart. A record a
 *  bullet opens stands in the track beside the page as the path the member took to it, so a second
 *  bullet takes the first record's place and reading two at once is asked for with a cmd- or
 *  middle-press. Each lane names the app it is read in, since a memory reaches the page through the
 *  app that filed it and one page draws several apps' memories. */
export function Wiki({
  place,
  onPlace,
}: {
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
          <Member id={member} viewer={viewer} opens={opens} onPlace={onPlace} />
        )}
      </div>

      {opens.map((id, at) => {
        const held = objectAt(id);
        const before = at === 0 ? null : objectAt(opens[at - 1]);
        if (held === null) return null;
        return (
          <RecordSlot
            key={id}
            id={id}
            held={held}
            from={before?.name ?? WIKI}
            opens={opens}
            onPlace={onPlace}
          />
        );
      })}
    </>
  );
}

/** One record the page opened, standing in the track as a panel. A link out of it opens the record
 *  it names immediately beside this one and ends the path there: whatever stood further right was
 *  reached through the record the member has just left.
 *
 *  `from` is what this one was opened out of — the record standing to its left, or the app's own
 *  page — said as the crumb over it. A lane paged one to a screen has nothing standing to its left,
 *  so that crumb is the only way back the member has. */
function RecordSlot({
  id,
  held,
  from,
  opens,
  onPlace,
}: {
  id: string;
  held: ObjectAddress;
  from: string;
  opens: string[];
  onPlace: (place: Placement) => void;
}) {
  const shut = () => onPlace({ opens: closed(opens, id) });
  return useSlot(
    <ObjectDetail
      agentId={held.agent}
      kind={held.kind}
      name={held.name}
      onOpen={(next) => onPlace({ opens: opened(opens, slotOf(next), id) })}
      onBack={shut}
    />,
    { id, kind: "panel", title: held.name, parent: { label: from, onGo: shut }, onClose: shut },
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
          <RebuildDialog
            title="Rebuild Page Facts"
            action="Rebuild page facts"
            verb="rebuild_page_facts"
          >
            <p className="m-0">
              Rows derived from synced pages are written again from those pages, as the derivation
              pass reaches each one. No row is dropped before its replacement is written.
            </p>
            <p className="m-0">
              Overview summaries are not rebuilt. The consolidation pass writes them from facts that
              agree, and re-forms them on its own as facts age into a cluster.
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
  const summary = usePanelRead<MemoryPayload>(
    "/workspace/memory?kind=" + SUMMARY_CLASS,
    reloads,
  );
  const members = usePanelRead<MembersPayload>(
    "/objects/" + MEMBER_KIND,
    reloads,
  );
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
        viewer={viewer}
        onOpen={(id) => onPlace({ opens: opened(opens, MEMBER_PREFIX + id) })}
      />

      {WORKSPACE_TOPICS.map((topic) => (
        <Topic
          key={topic.memoryKind}
          topic={topic}
          scope="shared"
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
  viewer,
  onOpen,
}: {
  state: ReturnType<typeof usePanelRead<MembersPayload>>;
  rows: MemberRow[];
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
            meta={(row) => [
              row.admin ? "Admin" : "Member",
              row.seated ? "Seated" : "No seat",
            ]}
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
  opens,
  onPlace,
}: {
  id: string;
  viewer: string | null;
  opens: string[];
  onPlace: (place: Placement) => void;
}) {
  const [reloads, setReloads] = useState(0);
  const { present, report, updated } = usePresence();
  const members = usePanelRead<MembersPayload>(
    "/objects/" + MEMBER_KIND,
    reloads,
  );
  const summary = usePanelRead<MemoryPayload>(
    "/workspace/memory?kind=" + SUMMARY_CLASS,
    reloads,
  );
  const found =
    members.phase === "ready"
      ? (distinct(members.payload.objects).find((row) => row.name === id) ??
        null)
      : null;
  const mine = found !== null && found.email === viewer;
  const entries: Entry[] = [
    ...(mine && present.overview
      ? [{ id: "overview", title: "Overview" }]
      : []),
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
      parent={{
        label: WIKI,
        onGo: () => onPlace({ opens: closed(opens, MEMBER_PREFIX + id) }),
      }}
      title={found?.email ?? MEMBER}
      acts={<Acts updated={updated} onReload={() => setReloads((run) => run + 1)} />}
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
          <Overview state={summary} scope="own" onPresent={report} />
          {MEMBER_TOPICS.map((topic) => (
            <Topic
              key={topic.memoryKind}
              topic={topic}
              scope="own"
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
  state: ReturnType<typeof usePanelRead<MemoryPayload>>;
  scope: Scope;
  onPresent: (id: string, drawn: boolean, newest: string | null) => void;
}) {
  const matches =
    state.phase === "ready" && state.payload.available
      ? inScope(state.payload.matches, scope)
      : [];
  const absent = state.phase === "ready" && !state.payload.available;
  const drawn = matches.length > 0 || absent || state.phase === "failed";
  const newest =
    matches
      .map((match) => match.created_at)
      .filter((at): at is string => at !== null)
      .sort()
      .at(-1) ?? null;
  useEffect(() => {
    onPresent("overview", drawn, newest);
  }, [onPresent, drawn, newest]);
  if (state.phase === "loading") return null;
  if (state.phase === "failed") {
    return (
      <Band id="overview" title="Overview" note={overviewNote(state, scope)}>
        <Panel state={state}>{() => null}</Panel>
      </Band>
    );
  }
  if (absent) {
    return (
      <Band id="overview" title="Overview" note={overviewNote(state, scope)}>
        <PanelBlank body={MEMORY_ABSENT} />
      </Band>
    );
  }
  if (matches.length === 0) return null;
  return (
    <Band id="overview" title="Overview" note={overviewNote(state, scope)}>
      {matches.map((match) => (
        <p key={match.ref ?? match.text}>{match.text}</p>
      ))}
    </Band>
  );
}

/** How current the account is, stamped once over the whole of it: what a reader is deciding is
 *  whether to trust this page at all, which the oldest line on it cannot answer. */
function overviewNote(
  state: ReturnType<typeof usePanelRead<MemoryPayload>>,
  scope: Scope,
): string {
  const written =
    scope === "shared"
      ? "Written from the workspace's facts once enough of them agree."
      : "Written from your facts once enough of them agree.";
  if (state.phase !== "ready" || !state.payload.available) return written;
  const stamps = inScope(state.payload.matches, scope)
    .map((match) => match.created_at)
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
  reloads,
  opens,
  from,
  onPresent,
  onPlace,
}: {
  topic: TopicSpec;
  scope: Scope;
  reloads: number;
  opens: string[];
  /** The page a bullet is pressed on, which every record it opens stands after. */
  from?: string;
  onPresent: (id: string, drawn: boolean, newest: string | null) => void;
  onPlace: (place: Placement) => void;
}) {
  const state = usePanelRead<ObjectsPayload>(
    "/objects/" +
      MEMORY_KIND +
      "?memory_kind=" +
      topic.memoryKind +
      "&" +
      NEWEST_FIRST,
    reloads,
  );
  const rows =
    state.phase === "ready"
      ? inScope(distinct(state.payload.objects), scope).slice(0, TOPIC_ROWS)
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
  return (
    <Band id={topic.memoryKind} title={topic.title} note={topic.note}>
      <ul>
        {rows.map((row) => {
          const id = slotOf({ agent: row.agent_id, kind: MEMORY_KIND, name: row.name });
          const standing = opens.includes(id);
          const press = (event: MouseEvent<HTMLButtonElement>) =>
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
