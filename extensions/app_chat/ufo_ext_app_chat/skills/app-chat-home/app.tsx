// The chat app's page: TSX compiled in the browser by the portal's app kit. Everything it
// composes comes off the UfoAppKit global; edit this file and redeploy to change the page.

const {
  COLUMN,
  ChatPane,
  ConversationDetail,
  Header,
  Moment,
  Page,
  Pane,
  PaneNote,
  React,
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
  useState,
} = UfoAppKit;

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
  last_at: string;
};

/** The permalink resolve's row shape — a conversation the listing's own window no longer carries,
 *  or one held by an agent the listing never fans over, still opens by its address. */
type ResolvedChat = {
  conversation_id: string;
  agent_id: string;
  agent_name: string;
  title: string;
  surface: string;
  last_at: string;
};

type Shown =
  | { kind: "loading" }
  | { kind: "missing" }
  | { kind: "compose" }
  | { kind: "list"; rows: ConversationRow[]; walk: string | null }
  | { kind: "open"; row: ConversationRow }
  | { kind: "reading"; conversation: Conversation };

/** The conversation listing the page holds: walked once and again on a cursor page, and searched
 *  to open a row without a read of its own. */
type Listing =
  | { kind: "loading" }
  | { kind: "failed" }
  | { kind: "ready"; rows: ConversationRow[]; walk: string | null };

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
  const wanted = wantedIn(at);
  const after = at.after ?? "";
  // The listing, walked once and again only when the cursor pages — never when the open target
  // changes. Held so opening a row resolves against it rather than re-walking it, and so a row the
  // listing already carries — including a workspace-shared conversation the member does not own —
  // opens without a second read that would not find it.
  const [list, setList] = useState<Listing>({ kind: "loading" });
  useEffect(() => {
    let live = true;
    setList({ kind: "loading" });
    const params = new URLSearchParams({ order_by: "last_at", order: "desc", portal: "true" });
    if (after) params.set("cursor", after);
    void getJson<{ objects: ConversationRow[]; next_cursor: string | null }>(
      "/objects/conversation?" + params.toString(),
    ).then((answer) => {
      if (!live) return;
      setList(
        answer.ok
          ? { kind: "ready", rows: answer.payload.objects, walk: answer.payload.next_cursor }
          : { kind: "failed" },
      );
    });
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
      // A web chat comes back as a rail row this page can carry on. Another surface — a Slack
      // thread, a terminal session — comes back as a read-only conversation projection instead,
      // which the page shows as a transcript with the way back to the surface that holds it.
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
  const place = useCallback(
    (target: string | null) => {
      const next = target === null ? {} : { opens: [target] };
      setAt(next);
      navigate(agentHash(appId, next));
    },
    [appId],
  );
  const turn = useCallback(
    (token: string | undefined) => {
      const next = token === undefined ? {} : { after: token };
      setAt(next);
      navigate(agentHash(appId, next));
    },
    [appId],
  );
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
  if (shown.kind === "loading") return <PaneNote>Loading…</PaneNote>;
  if (shown.kind === "missing") {
    return <PaneNote>This conversation is not available here.</PaneNote>;
  }
  if (shown.kind === "list") {
    return (
      <Pane>
        <Header pinned heading={1} title="Chat" />
        <Page>
          <div className={COLUMN}>
            {shown.rows.length === 0 ? (
              <p className="m-0 text-ink-soft">No conversations yet.</p>
            ) : (
              <ul className="m-0 flex list-none flex-col gap-px p-0">
                {shown.rows.map((row) => (
                  <li key={row.name}>
                    <button
                      type="button"
                      onClick={() => place(row.name)}
                      className={cn(
                        "flex h-(--size-row) w-full items-center gap-md rounded-row border-0",
                        "bg-transparent px-sm text-left text-inherit hover:bg-fill",
                      )}
                    >
                      <span className="min-w-0 flex-1 truncate">{row.title}</span>
                      <span className="shrink-0 text-label text-ink-soft">
                        {agentName(row.agent_name)}
                      </span>
                      <span className="shrink-0 font-mono text-small text-ink-soft">
                        <Moment at={row.last_at} />
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {shown.walk || after ? (
              <div className="flex gap-xs">
                {shown.walk ? (
                  <button
                    type="button"
                    className={cn(
                      "rounded-row border-0 bg-transparent px-sm py-xs text-left",
                      "text-inherit hover:bg-fill",
                    )}
                    onClick={() => turn(shown.walk ?? undefined)}
                  >
                    Older conversations
                  </button>
                ) : null}
                {after ? (
                  <button
                    type="button"
                    className={cn(
                      "rounded-row border-0 bg-transparent px-sm py-xs text-left",
                      "text-inherit hover:bg-fill",
                    )}
                    onClick={() => turn(undefined)}
                  >
                    Newest conversations
                  </button>
                ) : null}
              </div>
            ) : null}
          </div>
        </Page>
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
