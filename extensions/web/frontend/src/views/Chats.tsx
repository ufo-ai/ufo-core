import { IconDots } from "@tabler/icons-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Search } from "@/components/ui/field";
import { Segmented } from "@/components/ui/filter";
import { ACTS, TdActs, TdFact, TdFill, TdWhole } from "@/components/ui/table";
import { BANDS, COLUMN, FacetMenu, Header, Page, PageToolbar, Pane } from "@/kernel/pane";
import type { FacetGroup } from "@/kernel/pane";
import { Notice, PanelEmpty, PanelSkeleton, Section, usePanelRead } from "@/kernel/panel";
import type { PanelState } from "@/kernel/panel";
import { DataTable } from "@/kernel/table";
import { postObjectAction } from "@/lib/api";
import { SHARED_SUBJECT, ownerLabel, useViewer } from "@/lib/audience";
import {
  AutomationMark,
  ChannelMark,
  ChatStatus,
  ShareMark,
  channelWord,
} from "@/lib/chatMark";
import { cn } from "@/lib/cn";
import { Moment } from "@/lib/moments";
import { OwnerMark } from "@/lib/ownerMark";
import {
  CHAT_STATE_RANK,
  chatRows,
  chatState,
  type ConversationsPayload,
} from "@/lib/rail";
import { railFiled, readRail, useRail } from "@/lib/railStore";
import type { PlaceStep, WorkspacePlace } from "@/lib/route";
import type { Conversation } from "@/lib/types";
import { HOME_TITLE } from "@/lib/title";

const COLUMNS = [
  { label: "Chat", fill: true },
  { label: "Owner", whole: true },
  { label: "Time", fact: true },
  { label: "", fact: true },
];

const NO_CHATS = "No conversations yet.";
const NO_MATCHES = "No conversations match.";
const UNREADABLE = "Couldn't load conversations.";
const SEARCH = "Search chats";
const CUT = "Search to reach older chats.";
const ARCHIVED = "Archived";

const CONVERSATION_KIND = "conversation";
const ARCHIVE_ACTION = "archive_conversation";
const UNARCHIVE_ACTION = "unarchive_conversation";
const PIN_ACTION = "pin_conversation";
const UNPIN_ACTION = "unpin_conversation";
const DELETE_ACTION = "delete_conversation";

/** The acts that take a conversation out of the listing the main table draws. */
const AWAY_ACTIONS = [ARCHIVE_ACTION, DELETE_ACTION];

/** Every cell but the time states a fact the member came to read, so the table is drawn in the
 *  page's own ink and the stamp alone recedes. */
const FACT = "text-ink";

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

type Scope = "all" | "mine" | "workspace";

const SCOPE_SEGMENTS: { label: string; value: Scope }[] = [
  { label: "All", value: "all" },
  { label: "Mine", value: "mine" },
  { label: "Workspace", value: "workspace" },
];

function asScope(value: string | undefined): Scope {
  return value === "mine" || value === "workspace" ? value : "all";
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

/** The rows arrive newest `last_at` first and a sort holds equal keys in the order it was given
 *  them, so recency orders each state's own run without being a key here. A pinned row leads. */
function ordered(rows: Conversation[]): Conversation[] {
  return [...rows].sort(
    (one, two) =>
      Number(two.pinned) - Number(one.pinned) ||
      CHAT_STATE_RANK[chatState(one)] - CHAT_STATE_RANK[chatState(two)],
  );
}

/** What a row's act filed, so the listing answers for the conversation before the reads do. */
type Filed = (conversationId: string, action: string) => void;

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
  const [filings, setFilings] = useState(0);
  const filed = useCallback((conversationId: string, action: string) => {
    if (AWAY_ACTIONS.includes(action)) railFiled(conversationId);
    readRail();
    setFilings((held) => held + 1);
  }, []);
  const said = usePanelRead<ConversationsPayload>(
    query ? conversationsRead(query, after) : null,
    filings,
  );
  const away = usePanelRead<ConversationsPayload>(archivedRead(query), filings);
  const archived = away.phase === "ready" ? chatRows(away.payload) : [];
  /** The archived walk holds no step of its own, so a page that stops short — cut, or holding a
   *  cursor nothing reads — says so instead of truncating. */
  const awayCut =
    away.phase === "ready" && (away.payload.cut === true || Boolean(away.payload.next_cursor));
  const read: Read = query
    ? {
        phase: said.phase,
        rows: said.phase === "ready" ? chatRows(said.payload) : [],
        /** A listing holding a step to the rest is not cut: the step is the way to them. */
        cut: said.phase === "ready" && said.payload.cut === true && !said.payload.next_cursor,
        older: said.phase === "ready" ? said.payload.next_cursor : null,
      }
    : { phase: rail.phase, rows: rail.rows, cut: rail.cut, older: null };
  const shown = read.rows.filter((row) => scoped(row, scope));
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
              </span>
            </PageToolbar>
            <Section>
              <Listing
                read={read}
                rows={ordered(shown.filter((row) => faceted(row, picked)))}
                narrowed={narrowed}
                after={place.after}
                onPlace={(stepped) => onPlace({ ...place, after: stepped }, "push")}
                onOpen={onOpen}
                onFiled={filed}
              />
            </Section>
            {archived.length ? (
              <Section title={ARCHIVED}>
                <DataTable
                  columns={COLUMNS}
                  rows={archived.filter((row) => scoped(row, scope) && faceted(row, picked))}
                  rowKey={(row) => row.conversation_id}
                  lede
                  empty={NO_CHATS}
                  note={NO_MATCHES}
                  open={(row) => () => onOpen(row)}
                >
                  {(row) => <ChatCells row={row} onFiled={filed} />}
                </DataTable>
                {awayCut ? <Notice>{CUT}</Notice> : null}
              </Section>
            ) : null}
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

function Listing({
  read,
  rows,
  narrowed,
  after,
  onPlace,
  onOpen,
  onFiled,
}: {
  read: Read;
  rows: Conversation[];
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
      rows={rows}
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

function ChatCells({ row, onFiled }: { row: Conversation; onFiled: Filed }) {
  return (
    <>
      <TdFill className={FACT}>
        <span className="flex min-w-0 items-center gap-sm">
          <ChatStatus row={row} />
          <span className="truncate">{row.title}</span>
          <span className="ml-auto flex shrink-0 items-center gap-2xs">
            <ChannelMark row={row} />
            {row.automation_name ? <AutomationMark row={row} /> : null}
            <ShareMark row={row} />
          </span>
        </span>
      </TdFill>
      <TdWhole className={FACT}>
        <OwnerMark name={row.owner_name} email={row.owner_email} />
      </TdWhole>
      <TdFact>
        <Moment at={row.last_at} />
      </TdFact>
      <TdActs>
        <div className={ACTS}>
          <RowActs row={row} onFiled={onFiled} />
        </div>
      </TdActs>
    </>
  );
}

/** Delete is offered on a conversation this member owns and on no other, because the verb refuses
 *  anybody else; a control that refuses on press is a control that should not have been drawn. */
function RowActs({ row, onFiled }: { row: Conversation; onFiled: Filed }) {
  const acting = useViewer();
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState(false);
  const [refused, setRefused] = useState("");
  const owned =
    acting !== null && (row.owner_email === acting || (row.mine && row.owner_email === null));
  const file = async (action: string) => {
    setBusy(true);
    setRefused("");
    const outcome = await postObjectAction(
      row.agent_id,
      { kind: CONVERSATION_KIND, name: row.conversation_id, action },
      {},
    );
    setBusy(false);
    if (!outcome.applied) {
      setRefused(outcome.message);
      return;
    }
    setAsking(false);
    onFiled(row.conversation_id, action);
  };
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="quiet" size="icon" busy={busy} aria-label={"Actions for " + row.title}>
            <IconDots aria-hidden />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem
            onSelect={() => void file(row.archived ? UNARCHIVE_ACTION : ARCHIVE_ACTION)}
          >
            {row.archived ? "Unarchive" : "Archive"}
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => void file(row.pinned ? UNPIN_ACTION : PIN_ACTION)}>
            {row.pinned ? "Unpin" : "Pin"}
          </DropdownMenuItem>
          {owned ? (
            <DropdownMenuItem onSelect={() => setAsking(true)}>Delete</DropdownMenuItem>
          ) : null}
        </DropdownMenuContent>
      </DropdownMenu>
      {refused && !asking ? (
        <span role="status" className="text-label text-ink-soft">
          {refused}
        </span>
      ) : null}
      <Dialog open={asking} onOpenChange={setAsking}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete this conversation?</DialogTitle>
            <DialogDescription>
              {row.title} leaves every listing and only you, its owner, can restore it.
            </DialogDescription>
          </DialogHeader>
          {refused ? (
            <p role="status" className="m-0 text-small text-ink-soft">
              {refused}
            </p>
          ) : null}
          <DialogFooter>
            <Button busy={busy} onClick={() => void file(DELETE_ACTION)}>
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
