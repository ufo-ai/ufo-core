import { IconDotsVertical, IconPlus } from "@tabler/icons-react";
import { useCallback, useEffect, useState } from "react";

import { AvatarStack, type AvatarStackPerson } from "@/components/ui/avatar-stack";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Search } from "@/components/ui/field";
import { Segmented, type Segment } from "@/components/ui/filter";
import { ACTS, TdActs, TdFill, TdStamp, TdWhole } from "@/components/ui/table";
import { BANDS, COLUMN, FacetMenu, Header, Page, PageToolbar, Pane } from "@/kernel/pane";
import type { FacetGroup } from "@/kernel/pane";
import { Notice, PanelEmpty, PanelSkeleton, Section, usePanelRead } from "@/kernel/panel";
import type { PanelState } from "@/kernel/panel";
import { DataTable, type Run } from "@/kernel/table";
import { SHARED_SUBJECT, ownerLabel, speakerName, useViewer } from "@/lib/audience";
import {
  ArtifactMark,
  AutomationMark,
  ChannelMark,
  ChatStatus,
  PINNED,
  STATE_WORDS,
  ShareMark,
  channelWord,
} from "@/lib/chatMark";
import { cn } from "@/lib/cn";
import { useMainAgent } from "@/lib/mainAgent";
import { useMemberFaces, type MemberFaces } from "@/lib/memberDirectory";
import { Moment } from "@/lib/moments";
import { OwnerMark } from "@/lib/ownerMark";
import {
  ChatFilingDialog,
  ChatFilingItems,
  useChatFiling,
  type Filed,
} from "@/lib/chatFiling";
import {
  CHAT_STATES,
  FILING_MARKS,
  chatRows,
  chatState,
  mergeChats,
  type ConversationsPayload,
} from "@/lib/rail";
import { pickSectionShut, useRail } from "@/lib/railStore";
import { openNewChat } from "@/lib/router";
import type { PlaceStep, WorkspacePlace } from "@/lib/route";
import type { Conversation } from "@/lib/types";
import { HOME_TITLE } from "@/lib/title";

const COLUMNS = [
  { label: "Chat", fill: true },
  { label: "Creator", stamp: true },
  { label: "Members", whole: true },
  { label: "Channel", whole: true },
  { label: "Time", stamp: true },
  { label: "", acts: true },
];

const NO_CHATS = "No conversations yet.";
const NO_MATCHES = "No conversations match.";
const UNREADABLE = "Couldn't load conversations.";
const SEARCH = "Search chats";
const CUT = "Search to reach older chats.";
const ARCHIVED = "Archived";
const NEW = "New";

/** The rows this member's own acts have filed, by conversation, as each act left them. */
type Filings = Readonly<Record<string, Conversation>>;

const NO_FILINGS: Filings = {};

/** One listing's rows with the filings laid over the read behind it, so a row an act moved is
 *  drawn where the act put it while the read behind the listing catches up. */
function filedRows(rows: Conversation[], filings: Filings, archived: boolean): Conversation[] {
  const laid = rows.map((row) => {
    const filed = filings[row.conversation_id];
    return filed === undefined
      ? row
      : { ...row, archived: filed.archived, pinned: filed.pinned, deleted: filed.deleted };
  });
  return mergeChats(laid, Object.values(filings)).filter(
    (row) => !row.deleted && row.archived === archived,
  );
}

/** The filings this listing's read has caught up with: a row it names as the act left it, and a row
 *  the act moved off this listing that it no longer names. Every later read answers for them. */
function caught(rows: Conversation[], filings: Filings, archived: boolean): string[] {
  const named = new Map(rows.map((row) => [row.conversation_id, row]));
  return Object.keys(filings).filter((id) => {
    const read = named.get(id);
    const filed = filings[id];
    if (read === undefined) return filed.deleted || filed.archived !== archived;
    return (
      read.archived === filed.archived &&
      read.pinned === filed.pinned &&
      read.deleted === filed.deleted
    );
  });
}

/** The chat's own name is what the member came to read, so it is drawn in the page's own ink and
 *  every fact beside it recedes. */
const FACT = "text-ink";

const BAND_HELD = "chats:";

/** The listing behind the rail is bounded before it is paged, so narrowing the loaded rows answers
 *  `No conversations match` for a conversation that exists. */
function conversationsRead(query: string, after: string): string {
  const params = new URLSearchParams({ order_by: "last_at", order: "desc", q: query });
  if (after) params.set("cursor", after);
  return "/objects/conversation?" + params.toString();
}

/** The archived rows are their own read: the listing narrows on the mark before its bound, so a
 *  filter over the rail's rows would miss every archived conversation past that bound. */
function archivedRead(query: string): string {
  const params = new URLSearchParams({
    order_by: "last_at",
    order: "desc",
    q: query,
    archived: "true",
  });
  return "/objects/conversation?" + params.toString();
}

type Scope = "all" | "mine" | "workspace" | "archived";

const ARCHIVED_SCOPE: Scope = "archived";

/** The rule is the seam: the first three narrow one listing of what the member is in, and the
 *  fourth asks the workspace a different question. */
const SCOPE_SEGMENTS: Segment[] = [
  { label: "All", value: "all" },
  { label: "Mine", value: "mine" },
  { label: ARCHIVED, value: ARCHIVED_SCOPE },
  { label: "Workspace", value: "workspace", rule: true },
];

function asScope(value: string | undefined): Scope {
  return value === "mine" || value === "workspace" || value === ARCHIVED_SCOPE ? value : "all";
}

function scoped(row: Conversation, scope: Scope): boolean {
  if (scope === "mine") return row.mine;
  if (scope === "workspace") return row.audience === SHARED_SUBJECT;
  return true;
}

const OWNER_FACET = "owner:";
const CHANNEL_FACET = "channel:";

/** The word the member reads, not the surface behind it: every portal surface draws `Web`, so a
 *  facet keyed on the surface offers entries that differ in nothing they can see. */
function channelFacet(row: Conversation): string {
  return CHANNEL_FACET + channelWord(row.surface);
}

/** The held facet is offered whether or not the narrowed rows hold it, or a scope that excludes it
 *  leaves the table empty with the filter responsible nowhere on screen. */
function facets(rows: Conversation[], viewer: string | null, picked: string): FacetGroup[] {
  const owners = new Map<string, string>();
  const channels = new Map<string, string>();
  for (const row of rows) {
    if (row.owner_email) {
      owners.set(OWNER_FACET + row.owner_email, ownerLabel(row.owner_email, viewer));
    }
    channels.set(channelFacet(row), channelWord(row.surface));
  }
  if (picked.startsWith(OWNER_FACET) && !owners.has(picked)) {
    owners.set(picked, ownerLabel(picked.slice(OWNER_FACET.length), viewer));
  }
  if (picked.startsWith(CHANNEL_FACET) && !channels.has(picked)) {
    channels.set(picked, picked.slice(CHANNEL_FACET.length));
  }
  const named = (held: Map<string, string>) =>
    [...held]
      .map(([value, label]) => ({ label, value }))
      .sort((one, two) => one.label.localeCompare(two.label));
  return [
    { label: "Owner", options: named(owners) },
    { label: "Channel", options: named(channels) },
  ].filter((group) => group.options.length > 1);
}

/** A value the groups above cannot have minted admits no row rather than every row: an address
 *  carrying a stale facet says so, instead of listing as though it held none. */
function faceted(row: Conversation, picked: string): boolean {
  if (!picked) return true;
  if (picked.startsWith(OWNER_FACET)) return row.owner_email === picked.slice(OWNER_FACET.length);
  if (picked.startsWith(CHANNEL_FACET)) return channelFacet(row) === picked;
  return false;
}

/** The bands the table stands in: a pinned row leads whatever it is doing, and every other row falls
 *  under the one word for what it is doing now, in the order those states rank. The rows arrive
 *  newest `last_at` first and a band keeps them in the order it was given them, so recency runs
 *  inside every band without being written here. A state no row holds draws no band — a name over
 *  nothing is a run the member cannot open.
 *
 *  Which bands are shut is the member's, held by name in the same store the sidebar's sections use,
 *  so a run folded away is still folded on the next visit. The table qualifies its names there: the
 *  rail has a `Pinned` run of its own, and folding one is not folding the other. */
export function chatBands(rows: Conversation[], shut: string[]): Run<Conversation>[] {
  const held = rows.filter((row) => !row.pinned);
  return [
    { label: PINNED, rows: rows.filter((row) => row.pinned) },
    ...CHAT_STATES.map((state) => ({
      label: STATE_WORDS[state],
      rows: held.filter((row) => chatState(row) === state),
    })),
  ]
    .filter((band) => band.rows.length > 0)
    .map((band) => ({
      ...band,
      shut: shut.includes(BAND_HELD + band.label),
      onShut: (folded: boolean) => pickSectionShut(BAND_HELD + band.label, folded),
    }));
}

/** The rows behind the table, from whichever source answered for them. */
type Read = {
  phase: PanelState<unknown>["phase"];
  rows: Conversation[];
  cut: boolean;
  older: string | null | undefined;
};

/** A search asks the workspace; at rest the rail's rows are the source, which carry the live turn
 *  the status column draws. Both hand rows newest `last_at` first, the order the states below
 *  stand within. */
export function Chats({
  place,
  onPlace,
  onOpen,
}: {
  place: WorkspacePlace;
  onPlace: (place: WorkspacePlace, step: PlaceStep) => void;
  onOpen: (row: Conversation) => void;
}) {
  const viewer = useViewer();
  const scope = asScope(place.scope);
  const query = place.q ?? "";
  const picked = place.chip ?? "";
  const after = place.after ?? "";
  const rail = useRail();
  const filing = scope === ARCHIVED_SCOPE;
  const said = usePanelRead<ConversationsPayload>(
    query && !filing ? conversationsRead(query, after) : null,
  );
  const away = usePanelRead<ConversationsPayload>(filing ? archivedRead(query) : null);
  /** What the read behind the listing has answered, counted: a filing retires on a read that
   *  answered after the act, and the rail counts the reads of its own listing. */
  const answering = filing ? away : said;
  const [panelAnswers, setAnswers] = useState(0);
  useEffect(() => {
    if (answering.phase === "ready") setAnswers((count) => count + 1);
  }, [answering]);
  const answers = panelAnswers + rail.answers;
  /** The reads a filing may be laid over: a listing answers for one query and one page, so a
   *  filing held past a change to either would patch a listing whose read never covered its row. */
  const context = query + "\n" + after;
  const [held, setHeld] = useState<{ context: string; laid: number; filings: Filings }>({
    context,
    laid: answers,
    filings: NO_FILINGS,
  });
  const filings = held.context === context ? held.filings : NO_FILINGS;
  /** The act posts its verb, the marks it set are drawn at once, and one bounded page confirms
   *  them; every listing this screen reads carries the marks meanwhile. */
  const filed = useCallback(
    (row: Conversation, action: string) => {
      const marked = { ...row, ...FILING_MARKS[action] };
      setHeld((was) => ({
        context,
        laid: answers,
        filings: {
          ...(was.context === context ? was.filings : NO_FILINGS),
          [marked.conversation_id]: marked,
        },
      }));
    },
    [context, answers],
  );
  const awayRows = away.phase === "ready" ? chatRows(away.payload) : [];
  const saidRows = said.phase === "ready" ? chatRows(said.payload) : [];
  const listedRows = query ? saidRows : rail.rows;
  const archived: Read = {
    phase: away.phase,
    rows: filedRows(awayRows, filings, true),
    /** The archived walk holds no step of its own, so a page that stops short — cut, or holding a
     *  cursor nothing reads — says so instead of truncating. */
    cut: away.phase === "ready" && (away.payload.cut === true || Boolean(away.payload.next_cursor)),
    older: null,
  };
  const listed: Read = query
    ? {
        phase: said.phase,
        rows: filedRows(saidRows, filings, false),
        /** A listing holding a step to the rest is not cut: the step is the way to them. */
        cut: said.phase === "ready" && said.payload.cut === true && !said.payload.next_cursor,
        older: said.phase === "ready" ? said.payload.next_cursor : null,
      }
    : {
        phase: rail.phase,
        rows: filedRows(rail.rows, filings, false),
        cut: rail.cut,
        older: null,
      };
  const read: Read = filing ? archived : listed;
  const shown = read.rows.filter((row) => scoped(row, scope));
  const answered =
    answers > held.laid
      ? caught(filing ? awayRows : listedRows, filings, filing).join("\n")
      : "";
  useEffect(() => {
    if (!answered) return;
    const retired = new Set(answered.split("\n"));
    setHeld((was) => {
      const kept = Object.entries(was.filings).filter(([id]) => !retired.has(id));
      return kept.length === Object.keys(was.filings).length
        ? was
        : { ...was, filings: Object.fromEntries(kept) };
    });
  }, [answered]);
  const narrowed = query !== "" || scope !== "all" || picked !== "";
  return (
    <Pane>
      <section aria-label={HOME_TITLE} className="flex min-h-0 min-w-0 flex-1 flex-col">
        <Header heading={1} title={HOME_TITLE} pinned />
        <Page>
          <div className={cn(COLUMN, BANDS)}>
            <PageToolbar>
              <Segmented
                label={HOME_TITLE}
                segments={SCOPE_SEGMENTS}
                value={scope}
                onPick={(value) =>
                  onPlace(
                    { ...place, scope: value === "all" ? undefined : value, after: undefined },
                    "replace",
                  )
                }
              />
              <span className="ml-auto flex shrink-0 items-center gap-sm max-narrow:ml-0">
                <FacetMenu
                  groups={facets(shown, viewer, picked)}
                  value={picked}
                  onPick={(value) =>
                    onPlace({ ...place, chip: value || undefined, after: undefined }, "replace")
                  }
                />
                <ChatSearch
                  query={query}
                  onSearch={(said) =>
                    onPlace({ ...place, q: said || undefined, after: undefined }, "replace")
                  }
                />
                <NewChat />
              </span>
            </PageToolbar>
            <Section>
              <Listing
                read={read}
                runs={chatBands(
                  shown.filter((row) => faceted(row, picked)),
                  rail.sectionsShut,
                )}
                narrowed={narrowed}
                after={place.after}
                onPlace={(stepped) => onPlace({ ...place, after: stepped }, "push")}
                onOpen={onOpen}
                onFiled={filed}
              />
            </Section>
          </div>
        </Page>
      </section>
    </Pane>
  );
}

/** The field holds what is typed and the address what was asked for, so a member sees their own
 *  keys without a place change on every one. */
function ChatSearch({ query, onSearch }: { query: string; onSearch: (query: string) => void }) {
  const [typed, setTyped] = useState(query);
  useEffect(() => setTyped(query), [query]);
  return (
    <Search
      label={SEARCH}
      placeholder="Search"
      value={typed}
      onChange={(event) => setTyped(event.target.value)}
      onSubmit={() => onSearch(typed.trim())}
      onClear={() => {
        setTyped("");
        onSearch("");
      }}
    />
  );
}

/** The act the bar ends on. It stands past the search because it makes a record rather than
 *  narrowing the ones on the screen. */
function NewChat() {
  const agent = useMainAgent();
  if (!agent) return null;
  return (
    <Button variant="send" size="bar" onClick={() => openNewChat(agent.id)}>
      <IconPlus aria-hidden />
      {NEW}
    </Button>
  );
}

function Listing({
  read,
  runs,
  narrowed,
  after,
  onPlace,
  onOpen,
  onFiled,
}: {
  read: Read;
  runs: Run<Conversation>[];
  narrowed: boolean;
  after: string | undefined;
  onPlace: (after: string | undefined) => void;
  onOpen: (row: Conversation) => void;
  onFiled: Filed;
}) {
  if (!read.rows.length && read.phase === "loading") return <PanelSkeleton shape="table" />;
  if (!read.rows.length && read.phase === "failed") return <PanelEmpty>{UNREADABLE}</PanelEmpty>;
  return (
    <>
    <DataTable
      columns={COLUMNS}
      runs={runs}
      rowKey={(row) => row.conversation_id}
      lede
      empty={NO_CHATS}
      note={narrowed ? NO_MATCHES : undefined}
      open={(row) => () => onOpen(row)}
      pager={{
        payload: { older: read.older },
        after,
        onPlace: (stepped) => onPlace(stepped.after),
      }}
    >
      {(row) => <ChatCells row={row} onFiled={onFiled} />}
    </DataTable>
    {read.cut ? <Notice>{CUT}</Notice> : null}
    </>
  );
}

/** A speaker as the stack draws them: the face is the member the listing attributed the turns to,
 *  and the reported line is a name to print, never a row to look up. */
function speakerPerson(
  spoken: string,
  email: string | undefined,
  faces: MemberFaces,
): AvatarStackPerson {
  const face = faces(email);
  if (face === null) return { name: speakerName(spoken) };
  return { name: face.name || speakerName(spoken), email: face.email, photo_url: face.photo_url };
}

function ChatCells({ row, onFiled }: { row: Conversation; onFiled: Filed }) {
  const faces = useMemberFaces();
  return (
    <>
      <TdFill className={FACT}>
        <span className="flex min-w-0 items-center gap-sm">
          <ChatStatus row={row} />
          <span className="truncate">{row.title}</span>
          <span className="ml-auto flex shrink-0 items-center gap-2xs">
            {row.automation_name ? <AutomationMark row={row} /> : null}
            <ArtifactMark row={row} />
            <ShareMark subject={row.audience} />
          </span>
        </span>
      </TdFill>
      <TdStamp>
        <OwnerMark name={row.owner_name} email={row.owner_email} />
      </TdStamp>
      <TdWhole>
        <AvatarStack
          people={row.speakers.map((who, at) => speakerPerson(who, row.speaker_emails[at], faces))}
        />
      </TdWhole>
      <TdWhole>
        <ChannelMark row={row} />
      </TdWhole>
      <TdStamp>
        <Moment at={row.last_at} />
      </TdStamp>
      <TdActs>
        <div className={ACTS}>
          <RowActs row={row} onFiled={onFiled} />
        </div>
      </TdActs>
    </>
  );
}

function RowActs({ row, onFiled }: { row: Conversation; onFiled: Filed }) {
  const filing = useChatFiling(row, onFiled);
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="quiet"
            size="icon"
            busy={filing.busy}
            aria-label={"Actions for " + row.title}
          >
            <IconDotsVertical aria-hidden />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <ChatFilingItems row={row} filing={filing} />
        </DropdownMenuContent>
      </DropdownMenu>
      <ChatFilingDialog row={row} filing={filing} />
    </>
  );
}
