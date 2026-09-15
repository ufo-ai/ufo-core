
import {
  Button,
  COLUMN,
  ChatPane,
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  FoundingChat,
  Header,
  IMESSAGE_SURFACE,
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

function runs(
  rows: ConversationRow[],
  category: string,
  hidden: string[],
  now: Date,
): Run[] {
  const admitted = rows.filter((row) => admits(row, hidden));
  const own = admitted.filter((row) => row.mine);
  const theirs = admitted.filter((row) => !row.mine);
  const grouped = own.length ? bucketed(own, category, now) : [];
  return theirs.length ? grouped.concat({ label: OTHER_MEMBERS, rows: theirs }) : grouped;
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
  const [list, setList] = useState<Listing>({ kind: "loading" });
  const walk = useCallback(() => {
    let live = true;
    setList((held) =>
      held.kind === "ready" || held.kind === "refresh"
        ? { kind: "refresh", rows: held.rows, walk: held.walk }
        : held,
    );
    void (async () => {
      const gathered: ConversationRow[] = [];
      let cursor = after;
      for (let page = 1; ; page += 1) {
        const params = new URLSearchParams({ order_by: "last_at", order: "desc" });
        if (cursor) params.set("cursor", cursor);
        const answer = await getJson<{ objects: ConversationRow[]; next_cursor: string | null }>(
          "/objects/conversation?" + params.toString(),
        );
        if (!live) return;
        if (!answer.ok) {
          setList(
            gathered.length
              ? { kind: "ready", rows: gathered, walk: cursor || null }
              : { kind: "failed" },
          );
          return;
        }
        gathered.push(...answer.payload.objects);
        cursor = answer.payload.next_cursor ?? "";
        const done = !cursor || page >= CHAT_PAGES;
        setList({ kind: "ready", rows: [...gathered], walk: done ? cursor || null : null });
        if (done) return;
      }
    })();
    return () => {
      live = false;
    };
  }, [after]);
  useEffect(() => walk(), [walk]);
  // The rows the member reads stand while the listing walks again, so a walk that lands mid-read
  // never blanks the column.
  useEffect(() => {
    const beat = setInterval(walk, 5000);
    return () => clearInterval(beat);
  }, [walk]);

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
                <section key={run.label} className="flex flex-col gap-sm">
                  <h2 className="m-0 px-sm font-sans text-label font-medium text-ink-soft">
                    {run.label}
                  </h2>
                  <ul className="m-0 flex list-none flex-col gap-hair p-0">
                    {run.rows.map((row) => (
                      <li key={row.name}>
                        <button type="button" onClick={() => place(row.name)} className={ROW}>
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
                      </li>
                    ))}
                  </ul>
                </section>
              ))
            )}
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
