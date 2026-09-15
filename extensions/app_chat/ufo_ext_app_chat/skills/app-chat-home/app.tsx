
import {
  Button,
  COLUMN,
  ChatPane,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  FoundingChat,
  Header,
  IMESSAGE_SURFACE,
  IconDots,
  IconFilter2,
  Moment,
  PageToolbar,
  Pane,
  PaneNote,
  SLACK_SURFACE,
  SurfaceGlyph,
  UFO_SURFACE,
  Loading,
  agentHash,
  agentName,
  cn,
  founded,
  getJson,
  isPortalChat,
  mountApp,
  navigate,
  onPlaced,
  postObjectAction,
  routeIs,
  surfaceWord,
  useAppLinks,
  useCallback,
  useEffect,
  useMainAgent,
  useRef,
  useState,
} from "ufo/kit";
import type { Agent, Conversation, Crumb, Member, WorkspacePlace } from "ufo/kit";

const COMPOSE = "compose";

function wantedIn(place: WorkspacePlace): string | null {
  const target = place.opens?.[0] ?? null;
  return target === "new" ? COMPOSE : target;
}

type ConversationRow = {
  name: string;
  agent_id: string;
  agent_name: string;
  title: string;
  surface: string;
  surface_label: string | null;
  mine: boolean;
  speaker: string | null;
  owner_email: string | null;
  archived: boolean;
  pinned: boolean;
  last_at: string;
};

const SHOWN_OPTIONS: { surface: string; label: string }[] = [
  { surface: UFO_SURFACE, label: "Terminal" },
  { surface: SLACK_SURFACE, label: "Slack" },
  { surface: IMESSAGE_SURFACE, label: "iMessage" },
];

function admits(row: ConversationRow, hidden: string[]): boolean {
  return isPortalChat(row.surface) || !hidden.includes(row.surface);
}

const CATEGORIES: { value: string; label: string }[] = [
  { value: "", label: "Recency" },
  { value: "app", label: "App" },
];

const HELD_LADDER = "chat-ladder";
const HELD_HIDDEN = "chat-hidden";

function heldLadder(): string {
  return localStorage.getItem(HELD_LADDER) === "app" ? "app" : "";
}

function holdLadder(ladder: string): void {
  localStorage.setItem(HELD_LADDER, ladder);
}

function heldHidden(): string[] {
  return (localStorage.getItem(HELD_HIDDEN) ?? "").split(",").filter(Boolean);
}

function holdHidden(hidden: string[]): void {
  localStorage.setItem(HELD_HIDDEN, hidden.join(","));
}

const DAY_MS = 86_400_000;

const DATES = ["Today", "Yesterday", "Previous 7 days", "Previous 30 days", "Older"];

function dayOf(at: Date): number {
  return Date.UTC(at.getUTCFullYear(), at.getUTCMonth(), at.getUTCDate()) / DAY_MS;
}

function dateRun(raw: string, now: Date): string {
  const at = new Date(raw);
  if (Number.isNaN(at.getTime())) return "Older";
  const days = dayOf(now) - dayOf(at);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return "Previous 7 days";
  if (days < 30) return "Previous 30 days";
  return "Older";
}

type Run = { label: string; rows: ConversationRow[] };

const OTHER_MEMBERS = "Other members";

const ROW = cn(
  "flex h-(--size-row) w-full items-center gap-sm rounded-row border-0",
  "bg-transparent px-sm text-left text-inherit hover:bg-fill",
);

function origin(row: ConversationRow): string {
  return row.surface_label || surfaceWord(row.surface);
}

// Pinned rows lead the list under no heading of their own: a member who pinned a thread is looking
// for it at the top, and a heading over two rows costs more of the column than it names.
const PINNED = "";
const PINNED_SECTION_KEY = "pinned";
const ARCHIVED = "Archived";

function runs(
  rows: ConversationRow[],
  category: string,
  hidden: string[],
  now: Date,
): Run[] {
  const admitted = rows.filter((row) => admits(row, hidden));
  const pinned = admitted.filter((row) => row.pinned);
  const rest = admitted.filter((row) => !row.pinned);
  const own = rest.filter((row) => row.mine);
  const theirs = rest.filter((row) => !row.mine);
  const grouped = pinned.length ? [{ label: PINNED, rows: pinned }] : [];
  const dated = own.length ? grouped.concat(bucketed(own, category, now)) : grouped;
  return theirs.length ? dated.concat({ label: OTHER_MEMBERS, rows: theirs }) : dated;
}

function bucketed(rows: ConversationRow[], category: string, now: Date): Run[] {
  const named = (row: ConversationRow) =>
    category === "app" ? agentName(row.agent_name) : dateRun(row.last_at, now);
  const buckets = new Map<string, ConversationRow[]>();
  for (const row of rows) {
    const label = named(row);
    buckets.set(label, (buckets.get(label) ?? []).concat(row));
  }
  const order = category === "" ? DATES.filter((run) => buckets.has(run)) : [...buckets.keys()];
  return order.map((run) => ({ label: run, rows: buckets.get(run) ?? [] }));
}

type Shown =
  | { kind: "loading" }
  | { kind: "missing" }
  | { kind: "compose" }
  | { kind: "list"; rows: ConversationRow[]; walk: string | null }
  | { kind: "open"; conversation: Conversation };

type Listing =
  | { kind: "loading" }
  | { kind: "failed" }
  | { kind: "ready"; rows: ConversationRow[]; walk: string | null }
  | { kind: "refresh"; rows: ConversationRow[]; walk: string | null };

const CHAT_PAGES = 6;

/** One walk of the conversation listing, kept fresh: the pages the member's own cursor opens,
 *  narrowed server-side to the archived conversations or to the rest of them. The archive is its
 *  own read rather than a filter over the rows this one gathered, because a filter applied to a
 *  gathered stride leaves every archived conversation past that stride out of the section that
 *  names it. */
function useConversations(after: string, archived: boolean): [Listing, () => void] {
  const [held, setHeld] = useState<Listing>({ kind: "loading" });
  const walk = useCallback(() => {
    let live = true;
    setHeld((standing) =>
      standing.kind === "ready" || standing.kind === "refresh"
        ? { kind: "refresh", rows: standing.rows, walk: standing.walk }
        : standing,
    );
    void (async () => {
      const gathered: ConversationRow[] = [];
      let cursor = after;
      for (let page = 1; ; page += 1) {
        const params = new URLSearchParams({ order_by: "last_at", order: "desc" });
        if (archived) params.set("archived", "true");
        if (cursor) params.set("cursor", cursor);
        const answer = await getJson<{ objects: ConversationRow[]; next_cursor: string | null }>(
          "/objects/conversation?" + params.toString(),
        );
        if (!live) return;
        if (!answer.ok) {
          setHeld(
            gathered.length
              ? { kind: "ready", rows: gathered, walk: cursor || null }
              : { kind: "failed" },
          );
          return;
        }
        gathered.push(...answer.payload.objects);
        cursor = answer.payload.next_cursor ?? "";
        const done = !cursor || page >= CHAT_PAGES;
        setHeld({ kind: "ready", rows: [...gathered], walk: done ? cursor || null : null });
        if (done) return;
      }
    })();
    return () => {
      live = false;
    };
  }, [after, archived]);
  useEffect(() => walk(), [walk]);
  // The rows the member reads stand while the listing walks again, so a walk that lands mid-read
  // never blanks the column.
  useEffect(() => {
    const beat = setInterval(walk, 5000);
    return () => clearInterval(beat);
  }, [walk]);
  return [held, walk];
}

const ARCHIVE_ACTION = "archive_conversation";
const UNARCHIVE_ACTION = "unarchive_conversation";
const PIN_ACTION = "pin_conversation";
const UNPIN_ACTION = "unpin_conversation";
const DELETE_ACTION = "delete_conversation";

/** The acts a member takes on one conversation from its row: file it away, hold it above the
 *  others, or take it off the list. Each is the conversation kind's own action, posted on the
 *  agent the row belongs to, and the listing is read again once the act applied so the row lands
 *  in the section the server now puts it in.
 *
 *  Delete is offered on a conversation this member owns and on no other, because the verb refuses
 *  anybody else; a control that refuses on press is a control that should not have been drawn. */
function RowActs({
  row,
  member,
  onActed,
}: {
  row: ConversationRow;
  member: Member;
  onActed: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState(false);
  const [refused, setRefused] = useState("");
  const owned = row.owner_email !== null && row.owner_email === member.email;
  const act = async (action: string) => {
    setBusy(true);
    setRefused("");
    const outcome = await postObjectAction(
      row.agent_id,
      { kind: "conversation", name: row.name, action },
      {},
    );
    setBusy(false);
    if (!outcome.applied) {
      setRefused(outcome.message);
      return;
    }
    setAsking(false);
    onActed();
  };
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="row"
            size="icon"
            busy={busy}
            aria-label={"Actions for " + row.title}
            className="shrink-0 border-transparent text-ink-soft hover:bg-fill"
          >
            <IconDots aria-hidden />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem
            onSelect={() => void act(row.archived ? UNARCHIVE_ACTION : ARCHIVE_ACTION)}
          >
            {row.archived ? "Unarchive" : "Archive"}
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => void act(row.pinned ? UNPIN_ACTION : PIN_ACTION)}>
            {row.pinned ? "Unpin" : "Pin"}
          </DropdownMenuItem>
          {owned ? (
            <DropdownMenuItem onSelect={() => setAsking(true)}>Delete</DropdownMenuItem>
          ) : null}
        </DropdownMenuContent>
      </DropdownMenu>
      <Dialog open={asking} onOpenChange={setAsking}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete this conversation?</DialogTitle>
            <DialogDescription>
              {row.title} leaves every member's conversation list. Its transcript stays and
              retention closes it.
            </DialogDescription>
          </DialogHeader>
          {refused ? (
            <p role="status" className="m-0 text-small text-ink-soft">
              {refused}
            </p>
          ) : null}
          <DialogFooter>
            <Button busy={busy} onClick={() => void act(DELETE_ACTION)}>
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

/** One run of the list under its heading, each row opening the conversation and carrying the acts
 *  on it. The pinned run heads the column under no heading, so its section draws none. */
function RunSection({
  run,
  member,
  mainAgent,
  onOpen,
  onActed,
}: {
  run: Run;
  member: Member;
  mainAgent: Agent | null;
  onOpen: (conversationId: string) => void;
  onActed: () => void;
}) {
  return (
    <section className="flex flex-col gap-sm">
      {run.label ? (
        <h2 className="m-0 px-sm font-sans text-label font-medium text-ink-soft">{run.label}</h2>
      ) : null}
      <ul className="m-0 flex list-none flex-col gap-hair p-0">
        {run.rows.map((row) => (
          <li key={row.name} className="flex items-center gap-2xs">
            <button type="button" onClick={() => onOpen(row.name)} className={ROW}>
              <span className="min-w-0 flex-1 truncate">{row.title}</span>
              {isPortalChat(row.surface) ? null : (
                <span className="flex shrink-0 items-center gap-2xs text-label text-ink-soft">
                  <SurfaceGlyph surface={row.surface} />
                  {origin(row)}
                </span>
              )}
              {mainAgent && row.agent_id !== mainAgent.id ? (
                <span className="shrink-0 text-label text-ink-soft">
                  {agentName(row.agent_name)}
                </span>
              ) : null}
              <span className="shrink-0 font-mono text-small text-ink-soft">
                <Moment at={row.last_at} />
              </span>
            </button>
            <RowActs row={row} member={member} onActed={onActed} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function ChatApp({
  arrived,
  appId,
  member,
  agents,
  crumb,
  portal,
}: {
  arrived: WorkspacePlace;
  appId: string;
  member: Member;
  agents: Agent[];
  crumb?: Crumb;
  portal: string;
}) {
  const mainAgent = useMainAgent();
  const [at, setAt] = useState<WorkspacePlace>(arrived);
  const standing = useRef(at);
  standing.current = at;
  const wanted = wantedIn(at);
  const after = at.after ?? "";
  const [hidden, setHidden] = useState<string[]>(heldHidden);
  const [category, setCategory] = useState<string>(heldLadder);
  const [list, walk] = useConversations(after, false);
  const [archive, walkArchive] = useConversations("", true);
  const acted = useCallback(() => {
    walk();
    walkArchive();
  }, [walk, walkArchive]);

  const [shown, setShown] = useState<Shown>({ kind: "loading" });
  useEffect(() => {
    if (wanted !== null) return;
    setShown((held) =>
      list.kind === "ready" || list.kind === "refresh"
        ? { kind: "list", rows: list.rows, walk: list.walk }
        : list.kind === "failed"
          ? { kind: "missing" }
          : held.kind === "list"
            ? held
            : { kind: "loading" },
    );
  }, [wanted, list]);
  useEffect(() => {
    if (wanted === null) return;
    if (wanted === COMPOSE) {
      setShown({ kind: "compose" });
      return;
    }
    let live = true;
    setShown((held) =>
      held.kind === "open" && held.conversation.id === wanted ? held : { kind: "loading" },
    );
    void getJson<{ conversation: Conversation }>("/api/chats?conversation=" + wanted).then(
      (sought) => {
        if (!live) return;
        const conversation = sought.ok ? sought.payload.conversation : null;
        setShown(
          conversation && (conversation.readable || conversation.disclosable)
            ? { kind: "open", conversation }
            : { kind: "missing" },
        );
      },
    );
    return () => {
      live = false;
    };
  }, [wanted]);
  useEffect(() => onPlaced(setAt), []);
  const step = useCallback(
    (patch: WorkspacePlace) => {
      const next = { ...standing.current, ...patch };
      setAt(next);
      navigate(agentHash(appId, next));
    },
    [appId],
  );
  const place = useCallback(
    (target: string | null) => step({ opens: target === null ? undefined : [target] }),
    [step],
  );
  const turn = useCallback(
    (token: string | undefined) => step({ after: token, opens: undefined }),
    [step],
  );
  const show = useCallback((surface: string, admitted: boolean) => {
    setHidden((held) => {
      const next = admitted ? held.filter((name) => name !== surface) : [...held, surface];
      holdHidden(next);
      return next;
    });
  }, []);
  const categorize = useCallback((value: string) => {
    holdLadder(value);
    setCategory(value);
  }, []);
  useAppLinks(
    useCallback(
      (route) => {
        if (routeIs(route, "chat")) {
          place(route.conversationId);
          return true;
        }
        if (routeIs(route, "new-chat")) {
          place(COMPOSE);
          return true;
        }
        if (routeIs(route, "home")) {
          place(null);
          return true;
        }
        return false;
      },
      [place],
    ),
    portal,
  );
  if (shown.kind === "loading")
    return (
      <PaneNote>
        <Loading />
      </PaneNote>
    );
  if (shown.kind === "missing") {
    return <PaneNote>This conversation is not available here.</PaneNote>;
  }
  if (shown.kind === "list") {
    const drawn = runs(shown.rows, category, hidden, new Date());
    const archived =
      archive.kind === "ready" || archive.kind === "refresh"
        ? archive.rows.filter((row) => admits(row, hidden))
        : [];
    if (!mainAgent) return <PaneNote>No such app.</PaneNote>;
    return (
      <Pane>
        <Header pinned heading={1} title="Chat" />
        <FoundingChat
          agent={mainAgent}
          member={member}
          onCreated={(conversationId, title) => {
            founded(mainAgent.id, conversationId, title);
            place(conversationId);
          }}
        >
          <div className={cn(COLUMN, "flex flex-col gap-2xl p-2xl")}>
            <PageToolbar>
              <span className="ml-auto flex shrink-0 items-center gap-sm max-narrow:ml-0">
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      variant="row"
                      size="icon"
                      aria-label="Chats options"
                      className={cn(
                        "border-transparent text-ink-soft hover:bg-fill",
                        (category || hidden.length) && "bg-fill text-ink",
                      )}
                    >
                      <IconFilter2 aria-hidden />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuLabel>Sort by</DropdownMenuLabel>
                    <DropdownMenuRadioGroup value={category} onValueChange={categorize}>
                      {CATEGORIES.map((entry) => (
                        <DropdownMenuRadioItem key={entry.value || "recency"} value={entry.value}>
                          {entry.label}
                        </DropdownMenuRadioItem>
                      ))}
                    </DropdownMenuRadioGroup>
                    <DropdownMenuSeparator />
                    <DropdownMenuLabel>Show</DropdownMenuLabel>
                    {SHOWN_OPTIONS.map((option) => (
                      <DropdownMenuCheckboxItem
                        key={option.surface}
                        checked={!hidden.includes(option.surface)}
                        onCheckedChange={(next) => show(option.surface, next)}
                      >
                        {option.label}
                      </DropdownMenuCheckboxItem>
                    ))}
                  </DropdownMenuContent>
                </DropdownMenu>
              </span>
            </PageToolbar>
            {drawn.length === 0 ? (
              <p className="m-0 text-ink-soft">No conversations yet.</p>
            ) : (
              drawn.map((run) => (
                <RunSection
                  key={run.label || PINNED_SECTION_KEY}
                  run={run}
                  member={member}
                  mainAgent={mainAgent}
                  onOpen={place}
                  onActed={acted}
                />
              ))
            )}
            {archived.length ? (
              <RunSection
                run={{ label: ARCHIVED, rows: archived }}
                member={member}
                mainAgent={mainAgent}
                onOpen={place}
                onActed={acted}
              />
            ) : null}
            {shown.walk || after ? (
              <div className="flex gap-sm">
                {shown.walk ? (
                  <Button variant="quiet" size="bar" onClick={() => turn(shown.walk ?? undefined)}>
                    Older conversations
                  </Button>
                ) : null}
                {after ? (
                  <Button variant="quiet" size="bar" onClick={() => turn(undefined)}>
                    Newest conversations
                  </Button>
                ) : null}
              </div>
            ) : null}
          </div>
        </FoundingChat>
      </Pane>
    );
  }
  if (shown.kind === "open") {
    const convo = shown.conversation;
    const agent =
      agents.find((entry) => entry.id === convo.agent?.id) ??
      ({ id: convo.agent?.id ?? "", name: convo.agent?.name ?? "", model: "" } as Agent);
    return (
      <ChatPane
        key={convo.id}
        agent={agent}
        member={member}
        conversationId={convo.id}
        conversation={convo}
        crumb={crumb}
        onActivity={() => {}}
      />
    );
  }
  if (!mainAgent) return <PaneNote>No such app.</PaneNote>;
  return (
    <ChatPane
      key="new"
      agent={mainAgent}
      member={member}
      conversationId={null}
      focusComposer
      onCreated={(conversationId, title) => {
        if (mainAgent) founded(mainAgent.id, conversationId, title);
        place(conversationId);
      }}
      onActivity={() => {}}
    />
  );
}

mountApp(document.getElementById("root")!, (init, agents) => (
  <ChatApp
    arrived={init.place}
    appId={init.agentId}
    member={init.member}
    agents={agents}
    crumb={init.crumb}
    portal={init.portal}
  />
));
