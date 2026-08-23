// The chat app's page: TSX compiled in the browser by the portal's app kit. Everything it
// composes comes off the UfoAppKit global; edit this file and redeploy to change the page.

const {
  COLUMN,
  ChatPane,
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
  mountApp,
  navigate,
  onPlaced,
  routeIs,
  useAppLinks,
  useCallback,
  useEffect,
  useMainAgent,
  useState,
} = UfoAppKit;

/** The chat app's page. Bare, it is a simple list of the member's conversations; opened at one,
 *  that conversation whole; opened at the compose target, the start screen — composer and
 *  starters — on the main agent, and the conversation a send founds takes the page over. A chat
 *  link inside a reply opens here; every other link leaves over the bridge. */

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
  | { kind: "open"; row: ConversationRow };

function ChatApp({
  arrived,
  appId,
  member,
  agents,
}: {
  arrived: WorkspacePlace;
  appId: string;
  member: Member;
  agents: Agent[];
}) {
  const mainAgent = useMainAgent();
  const [at, setAt] = useState<WorkspacePlace>(arrived);
  const wanted = wantedIn(at);
  const after = at.after ?? "";
  const [shown, setShown] = useState<Shown>({ kind: "loading" });
  useEffect(() => {
    if (wanted === COMPOSE) {
      setShown({ kind: "compose" });
      return;
    }
    let live = true;
    setShown((held) => (wanted === null && held.kind === "list" ? held : { kind: "loading" }));
    const params = new URLSearchParams({ order_by: "last_at", order: "desc", portal: "true" });
    if (after) params.set("cursor", after);
    void getJson<{ objects: ConversationRow[]; next_cursor: string | null }>(
      "/objects/conversation?" + params.toString(),
    ).then(async (answer) => {
      if (!live) return;
      if (!answer.ok) {
        setShown({ kind: "missing" });
        return;
      }
      const rows = answer.payload.objects;
      if (wanted === null) {
        setShown({ kind: "list", rows, walk: answer.payload.next_cursor });
        return;
      }
      const row = rows.find((entry) => entry.name === wanted);
      if (row) {
        setShown({ kind: "open", row });
        return;
      }
      const sought = await getJson<{ chats: ResolvedChat[] }>(
        "/api/chats?conversation=" + wanted,
      );
      if (!live) return;
      const held = sought.ok ? sought.payload.chats[0] : undefined;
      setShown(
        held
          ? {
              kind: "open",
              row: {
                name: held.conversation_id,
                agent_id: held.agent_id,
                agent_name: held.agent_name,
                title: held.title,
                surface: held.surface,
                last_at: held.last_at,
              },
            }
          : { kind: "missing" },
      );
    });
    return () => {
      live = false;
    };
  }, [wanted, after]);
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
      onCreated={(conversationId, title) => {
        if (mainAgent) founded(mainAgent.id, conversationId, title);
        place(conversationId);
      }}
      onActivity={() => {}}
    />
  );
}

mountApp(document.getElementById("root")!, (init, agents) => (
  <ChatApp arrived={init.place} appId={init.agentId} member={init.member} agents={agents} />
));
