// The chat app's page: a static site built with the portal's app kit. Edit this file and
// redeploy to change the page.

import {
  Button,
  COLUMN,
  ChatPane,
  ConversationDetail,
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
  Page,
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

/** The chat app's page. Bare, it is a simple list of the member's conversations; opened at one,
 *  that conversation whole; opened at the compose target, the start screen — composer and
 *  starters — on the main agent, and the conversation a send founds takes the page over. A chat
 *  link inside a reply opens here; every other link leaves over the bridge.
 *
 *  Standing on one conversation, the band names it under the crumb back to the page. That crumb is
 *  the shell's own, carried in over the bridge, so the step it names is the step the portal's tab
 *  title names and the page states no second answer to where it stands — a conversation the list
 *  holds for another app is still read inside this one. */

const COMPOSE = "compose";

/** The conversation the pane's place asks this page to stand on: the first slot of its track, with
 *  the fresh sentinel read as the composer. The place carries every key the address does, and this
 *  page holds one of them. */
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

/** The surfaces the member can put away. A Slack thread, a terminal session and an iMessage
 *  exchange are conversations they had somewhere else, and they are still their conversations — so
 *  the list holds every one of them until it is asked not to. */
const SHOWN_OPTIONS: { surface: string; label: string }[] = [
  { surface: UFO_SURFACE, label: "Terminal" },
  { surface: SLACK_SURFACE, label: "Slack" },
  { surface: IMESSAGE_SURFACE, label: "iMessage" },
];

/** Whether a row's surface is drawn. Everything is, bar the surfaces the member has put away — so
 *  the address carries what they hid rather than what they kept, and an address carrying nothing is
 *  the whole of their history rather than none of it. A portal chat is never put away: the list is
 *  the portal's own. */
function admits(row: ConversationRow, hidden: string[]): boolean {
  return isPortalChat(row.surface) || !hidden.includes(row.surface);
}

/** Which ladder the list runs its rows in. Recency is the one the page opens on. */
const CATEGORIES: { value: string; label: string }[] = [
  { value: "", label: "Recency" },
  { value: "app", label: "App" },
];

const HELD_LADDER = "chat-ladder";
const HELD_HIDDEN = "chat-hidden";

/** The ladder and the put-away surfaces are standing choices rather than places: a member who asked
 *  to see their terminal sessions asked about their own history, and a list that forgot on the way
 *  to another screen and back would ask them again every visit. The cursor stays in the address,
 *  where a page of a listing belongs.
 *
 *  A browser holding nothing has put nothing away, which is every surface drawn. */
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

/** Which calendar day a stamp fell on, in UTC as it was sent — the same UTC every other stamp on
 *  this surface reads in, so no reader's zone moves a conversation across midnight. */
function dayOf(at: Date): number {
  return Date.UTC(at.getUTCFullYear(), at.getUTCMonth(), at.getUTCDate()) / DAY_MS;
}

/** Which day-run a stamp falls in, counted in whole calendar days rather than in elapsed hours: a
 *  conversation at one this morning and one at eleven last night are two days apart to a reader and
 *  two hours apart to a clock, and it is the reader the headings are for. Today and Yesterday are
 *  carved out first, so the runs below them hold the days they have left. A stamp ahead of now — a
 *  clock askew between two machines — reads as today rather than as a run of its own. */
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

/** One run of rows the list draws together, under the heading the category named it. */
type Run = { label: string; rows: ConversationRow[] };

const OTHER_MEMBERS = "Other members";

/** One row of the list. The act that starts a conversation wears it too: it is an entry in the list
 *  rather than a control beside it, so the column has one left edge and one pitch all the way
 *  down. */
const ROW = cn(
  "flex h-(--size-row) w-full items-center gap-sm rounded-row border-0",
  "bg-transparent px-sm text-left text-inherit hover:bg-fill",
);

/** Where a conversation came in, as a row and a heading each name it: the room the surface itself
 *  named, else the member's word for the surface. */
function origin(row: ConversationRow): string {
  return row.surface_label || surfaceWord(row.surface);
}

/** The runs the list draws, in order. The member's own conversations take the category's ladder — a
 *  date run, an app's name, or the source each came in on — and the readable ones their colleagues
 *  are in follow as one run at the foot, never subdivided and by recency under every category: a
 *  colleague's thread is read for what happened lately in it. A run is drawn only where it holds a
 *  row, and the rows inside one keep the recency the read handed them.
 *
 *  The date runs are named in a fixed order rather than the order their rows arrive in, so a week
 *  with nothing in it does not reorder the column. Every other category takes the order its first
 *  row appeared in, which under a recency read is the app or the source that spoke last. */
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

/** The permalink resolve's row shape — a conversation the listing's own window no longer carries,
 *  or one held by an agent the listing never fans over, still opens by its address. */
type ResolvedChat = {
  conversation_id: string;
  agent_id: string;
  agent_name: string;
  title: string;
  surface: string;
  surface_label: string | null;
  mine: boolean;
  speaker: string | null;
  last_at: string;
};

type Shown =
  | { kind: "loading" }
  | { kind: "missing" }
  | { kind: "compose" }
  | { kind: "list"; rows: ConversationRow[]; walk: string | null }
  | { kind: "open"; row: ConversationRow }
  | { kind: "reading"; conversation: Conversation };

/** The conversation listing the page holds: walked from the address's own cursor, and searched to
 *  open a row without a read of its own. `walk` is the cursor the step to the rest of the history
 *  carries, and is null while the stride is still gathering or has reached the end of the listing. */
type Listing =
  | { kind: "loading" }
  | { kind: "failed" }
  | { kind: "ready"; rows: ConversationRow[]; walk: string | null };

/** How many of the listing's own pages one page of this list gathers. The kind answers fifty rows
 *  to a read, which is a fraction of what a member scans to find the one conversation they want — so
 *  a step follows the continuation this many times and draws the lot as one page, and each row that
 *  lands is drawn as it arrives rather than behind the whole walk. The bound is a count of reads
 *  rather than of rows, so a listing answering one row and a cursor costs the same as one answering
 *  fifty. The address carries the cursor the walk stopped on, so the step to the rest of the history
 *  is a place and not a scroll. */
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
  // The place a patch lands on, readable from a callback that outlives the render that made it.
  const standing = useRef(at);
  standing.current = at;
  const wanted = wantedIn(at);
  const after = at.after ?? "";
  const [hidden, setHidden] = useState<string[]>(heldHidden);
  const [category, setCategory] = useState<string>(heldLadder);
  // The listing, walked once and again only when the cursor steps — never when the open target, the
  // category or the Show set changes, because none of them changes which rows the read answers.
  // Held so opening a row resolves against it rather than re-walking it, and so a row the listing
  // already carries — including a workspace-shared conversation the member does not own — opens
  // without a second read that would not find it.
  const [list, setList] = useState<Listing>({ kind: "loading" });
  useEffect(() => {
    let live = true;
    setList({ kind: "loading" });
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
          // The pages already gathered are the answer: a stride that stopped short states the rows
          // it has and offers the step that reaches the rest, rather than blanking a list the member
          // is reading because its sixth read did not land.
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

  const [shown, setShown] = useState<Shown>({ kind: "loading" });
  useEffect(() => {
    if (wanted === COMPOSE) {
      setShown({ kind: "compose" });
      return;
    }
    if (wanted === null) {
      // A cursor step is not a new screen: while the next page loads, the rows in hand stand rather
      // than flashing a skeleton over the list the member was reading.
      setShown((held) =>
        list.kind === "ready"
          ? { kind: "list", rows: list.rows, walk: list.walk }
          : list.kind === "failed"
            ? { kind: "missing" }
            : held.kind === "list"
              ? held
              : { kind: "loading" },
      );
      return;
    }
    if (list.kind === "loading") {
      setShown((held) => (held.kind === "open" && held.row.name === wanted ? held : { kind: "loading" }));
      return;
    }
    // Opening a conversation the listing already carries needs no read of its own — the row is in
    // hand, workspace-shared rows included. Only one the listing does not carry is read by its own
    // address, and a conversation no read answers states the miss.
    const carried = list.kind === "ready" ? list.rows.find((row) => row.name === wanted) : undefined;
    if (carried) {
      setShown({ kind: "open", row: carried });
      return;
    }
    let live = true;
    setShown((held) => (held.kind === "open" && held.row.name === wanted ? held : { kind: "loading" }));
    void getJson<{ chats: ResolvedChat[]; conversation?: Conversation }>(
      "/api/chats?conversation=" + wanted,
    ).then((sought) => {
      if (!live) return;
      const chat = sought.ok ? sought.payload.chats[0] : undefined;
      // A web chat comes back as a rail row this page can carry on. Another surface comes back as
      // its conversation projection, including whether the portal may continue it.
      const reading = sought.ok ? sought.payload.conversation : undefined;
      setShown(
        chat
          ? {
              kind: "open",
              row: {
                name: chat.conversation_id,
                agent_id: chat.agent_id,
                agent_name: chat.agent_name,
                title: chat.title,
                surface: chat.surface,
                surface_label: chat.surface_label,
                mine: chat.mine,
                speaker: chat.speaker,
                last_at: chat.last_at,
              },
            }
          : reading
            ? { kind: "reading", conversation: reading }
            : { kind: "missing" },
      );
    });
    return () => {
      live = false;
    };
  }, [wanted, list]);
  useEffect(() => onPlaced(setAt), []);
  // Every move the page makes is one place written whole. The narrowing and the category are the
  // list's own state and ride the address, so opening a conversation and coming back lands on the
  // list the member left rather than on the whole history under the default category.
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
  // A step to another stride answers with rows the page has not read, so the open conversation goes
  // with it: the list is what the member asked to see.
  const turn = useCallback(
    (token: string | undefined) => step({ after: token, opens: undefined }),
    [step],
  );
  // The Show set and the ladder both redraw the rows already in hand, so neither reads and neither
  // touches the stride or the open conversation. Each is written to this browser as it is taken.
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
        {/* The conversations stand where a transcript would and the chat entry holds the bottom: the
            list is a screen a member reads and then writes from, so what they write starts the next
            conversation without their leaving for another screen. */}
        <FoundingChat
          agent={mainAgent}
          member={member}
          onCreated={(conversationId, title) => {
            founded(mainAgent.id, conversationId, title);
            place(conversationId);
          }}
        >
          {/* The bands — the controls and the runs of rows — are one column with one rhythm, so a
              run's heading sits nearer its own rows than the run above it. */}
          <div className={cn(COLUMN, "flex flex-col gap-2xl p-2xl")}>
            {/* The narrowings behind one glyph, drawn the way every other listing in the portal
                draws them. A sort is a pick between ladders and shuts the menu; a surface is a
                choice turned on and off and leaves it standing, so a member names both in one
                visit. */}
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
                          {/* Where the conversation came in, as the mark and the words for it. A
                              portal chat draws neither: the list is read in the portal, so a source
                              on every row would state where the member already is. */}
                          {isPortalChat(row.surface) ? null : (
                            <span className="flex shrink-0 items-center gap-2xs text-label text-ink-soft">
                              <SurfaceGlyph surface={row.surface} />
                              {origin(row)}
                            </span>
                          )}
                          {/* The app holding the conversation, where it is not the one whose page
                              this is: a row naming Chat on the chat app's own list would state the
                              screen the member is already looking at. */}
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
    const agent =
      agents.find((entry) => entry.id === shown.row.agent_id) ??
      ({ id: shown.row.agent_id, name: shown.row.agent_name, model: "" } as Agent);
    return (
      <ChatPane
        key={shown.row.name}
        agent={agent}
        member={member}
        conversationId={shown.row.name}
        title={shown.row.title}
        crumb={crumb}
        onActivity={() => {}}
      />
    );
  }
  if (shown.kind === "reading") {
    const convo = shown.conversation;
    const agent =
      agents.find((entry) => entry.id === convo.agent?.id) ??
      ({ id: convo.agent?.id ?? "", name: convo.agent?.name ?? "", model: "" } as Agent);
    if (convo.commentable) {
      return (
        <ChatPane
          key={convo.id}
          agent={agent}
          member={member}
          conversationId={convo.id}
          title={convo.description}
          crumb={crumb}
          onActivity={() => {}}
        />
      );
    }
    return (
      <Pane>
        <Header pinned heading={1} title={convo.description} />
        <Page>
          <div className={COLUMN}>
            <ConversationDetail agent={agent} conversation={convo} headed />
            <p className="m-0 max-w-hint text-ink-soft">
              {isPortalChat(convo.surface)
                ? "This conversation is read-only."
                : "This conversation is read-only here. Reply in " +
                  surfaceWord(convo.surface) +
                  " to continue it."}
            </p>
          </div>
        </Page>
      </Pane>
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
