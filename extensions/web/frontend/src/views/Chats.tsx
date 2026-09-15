import { useEffect, useState } from "react";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Search } from "@/components/ui/field";
import { Segmented } from "@/components/ui/filter";
import { TdFact, TdFill, TdWhole } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Pager } from "@/kernel/pager";
import { Header, PageToolbar, Pane } from "@/kernel/pane";
import { Notice, PanelEmpty, PanelSkeleton, Section, usePanelRead } from "@/kernel/panel";
import { DataTable } from "@/kernel/table";
import { SHARED_SUBJECT } from "@/lib/audience";
import { AutomationMark, ChannelMark, ChatStatus } from "@/lib/chatMark";
import { cn } from "@/lib/cn";
import { Moment } from "@/lib/moments";
import { chatRows, type ChatRow, type ConversationsPayload } from "@/lib/rail";
import { useRail } from "@/lib/railStore";
import type { PlaceStep, WorkspacePlace } from "@/lib/route";
import { HOME_TITLE } from "@/lib/title";

const COLUMNS = [
  { label: "Chat", fill: true },
  { label: "Owner", whole: true },
  { label: "Channel", fact: true },
  { label: "Time", fact: true },
];

const NO_CHATS = "No conversations yet.";
const NO_MATCHES = "No conversations match.";
const UNREADABLE = "Couldn't load conversations.";
const SEARCH = "Search chats";
const CUT = "Showing your most recent conversations. Search to reach the rest.";

/** The listing behind the rail is bounded before it is paged, so narrowing the loaded rows answers
 *  `No conversations match` for a conversation that exists. */
function conversationsRead(query: string, after: string): string {
  const params = new URLSearchParams({ order_by: "last_at", order: "desc", q: query });
  if (after) params.set("cursor", after);
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

const MEMBER_MARKS = [
  "bg-member-1 text-member-1-ink",
  "bg-member-2 text-member-2-ink",
  "bg-member-3 text-member-3-ink",
];

const FNV_OFFSET = 2166136261;
const FNV_PRIME = 16777619;

/** Off the top bits: `% 6` over FNV-1a's low ones put two addresses at one company on one colour,
 *  and `Math.abs` folds two hashes onto one number at the sign bit. */
function memberMark(email: string): string {
  let hash = FNV_OFFSET;
  for (const character of email.toLowerCase()) {
    hash = Math.imul(hash ^ character.codePointAt(0)!, FNV_PRIME);
  }
  const spread = (hash >>> 0) / 2 ** 32;
  return MEMBER_MARKS[Math.floor(spread * MEMBER_MARKS.length)];
}

function owner(row: ChatRow): string {
  return row.owner_name || row.owner_email || "";
}

/** One letter, because a second initial is a name the portal does not hold. The name a surface
 *  reported goes under the pointer: the address beside it is the fact every row carries. */
function Owner({ row }: { row: ChatRow }) {
  if (!row.owner_email) return null;
  const mark = (
    <span className="flex items-center gap-sm">
      <Avatar aria-label={owner(row)}>
        <AvatarFallback className={cn("font-medium", memberMark(row.owner_email))}>
          {owner(row).slice(0, 1).toUpperCase()}
        </AvatarFallback>
      </Avatar>
      {row.owner_email}
    </span>
  );
  if (!row.owner_name) return mark;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{mark}</TooltipTrigger>
      <TooltipContent>{row.owner_name}</TooltipContent>
    </Tooltip>
  );
}

function scoped(row: ChatRow, scope: Scope): boolean {
  if (scope === "mine") return row.mine;
  if (scope === "workspace") return row.audience === SHARED_SUBJECT;
  return true;
}

/** The rows are the rail's own, so the screen costs no read of its own and its status column
 *  moves as turns land. */
export function Chats({
  place,
  onPlace,
  onOpen,
}: {
  place: WorkspacePlace;
  onPlace: (place: WorkspacePlace, step: PlaceStep) => void;
  onOpen: (row: ChatRow) => void;
}) {
  const scope = asScope(place.scope);
  const query = place.q ?? "";
  return (
    <Pane>
      <section aria-label={HOME_TITLE} className="flex min-h-0 min-w-0 flex-1 flex-col">
        <Header heading={1} title={HOME_TITLE} pinned />
        <div className="flex min-h-0 flex-1 flex-col gap-2xl overflow-y-auto p-2xl">
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
              <ChatSearch
                query={query}
                onSearch={(said) =>
                  onPlace({ ...place, q: said || undefined, after: undefined }, "replace")
                }
              />
            </span>
          </PageToolbar>
          <Section>
            <Listing place={place} onPlace={onPlace} onOpen={onOpen} />
          </Section>
        </div>
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

/** A search asks the workspace; at rest the rail's rows are the source, which carry the live turn
 *  the status column draws. Both hand rows newest `last_at` first, the order the table stands in. */
function Listing({
  place,
  onPlace,
  onOpen,
}: {
  place: WorkspacePlace;
  onPlace: (place: WorkspacePlace, step: PlaceStep) => void;
  onOpen: (row: ChatRow) => void;
}) {
  const scope = asScope(place.scope);
  const query = place.q ?? "";
  const after = place.after ?? "";
  const rail = useRail();
  const said = usePanelRead<ConversationsPayload>(query ? conversationsRead(query, after) : null);
  const read = query
    ? {
        phase: said.phase,
        rows: said.phase === "ready" ? chatRows(said.payload) : [],
        /** A listing holding a step to the rest is not cut: the step is the way to them. */
        cut: said.phase === "ready" && said.payload.cut === true && !said.payload.next_cursor,
        older: said.phase === "ready" ? said.payload.next_cursor : null,
      }
    : { phase: rail.phase, rows: rail.rows, cut: rail.cut, older: null };
  if (!read.rows.length && read.phase === "loading") return <PanelSkeleton shape="table" />;
  if (!read.rows.length && read.phase === "failed") return <PanelEmpty>{UNREADABLE}</PanelEmpty>;
  const rows = read.rows.filter((row) => scoped(row, scope));
  return (
    <>
      <DataTable
        columns={COLUMNS}
        rows={rows}
        rowKey={(row) => row.conversation_id}
        lede
        empty={NO_CHATS}
        note={scope !== "all" || query !== "" ? NO_MATCHES : undefined}
        open={(row) => () => onOpen(row)}
      >
        {(row) => (
          <>
            <TdFill>
              <span className="flex min-w-0 items-center gap-sm">
                <ChatStatus row={row} />
                <span className="truncate">{row.title}</span>
                {row.automation_name ? <AutomationMark row={row} className="ml-auto" /> : null}
              </span>
            </TdFill>
            <TdWhole>
              <Owner row={row} />
            </TdWhole>
            <TdFact>
              <ChannelMark row={row} />
            </TdFact>
            <TdFact>
              <Moment at={row.last_at} />
            </TdFact>
          </>
        )}
      </DataTable>
      <Pager
        payload={{ older: read.older }}
        onPlace={(stepped) => onPlace({ ...place, after: stepped.after }, "push")}
      />
      {read.cut ? <Notice>{CUT}</Notice> : null}
    </>
  );
}
