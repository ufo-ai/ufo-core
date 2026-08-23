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
  isPortalChat,
  mountApp,
  navigate,
  onOpenTarget,
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

type Shown =
  | { kind: "loading" }
  | { kind: "missing" }
  | { kind: "compose" }
  | { kind: "list"; rows: ChatRow[] }
  | { kind: "open"; row: ChatRow };

function ChatApp({
  open,
  appId,
  member,
  agents,
}: {
  open: string | null;
  appId: string;
  member: Member;
  agents: Agent[];
}) {
  const mainAgent = useMainAgent();
  const [wanted, setWanted] = useState<string | null>(open === "new" ? COMPOSE : open);
  const [shown, setShown] = useState<Shown>({ kind: "loading" });
  useEffect(() => {
    if (wanted === COMPOSE) {
      setShown({ kind: "compose" });
      return;
    }
    let live = true;
    setShown({ kind: "loading" });
    void getJson<{ chats: ChatRow[] }>("/api/chats").then((answer) => {
      if (!live) return;
      if (!answer.ok) {
        setShown({ kind: "missing" });
        return;
      }
      const rows = answer.payload.chats.filter((entry) => isPortalChat(entry.surface));
      if (wanted === null) {
        setShown({ kind: "list", rows });
        return;
      }
      const row = rows.find((entry) => entry.conversation_id === wanted);
      setShown(row ? { kind: "open", row } : { kind: "missing" });
    });
    return () => {
      live = false;
    };
  }, [wanted]);
  useEffect(() => onOpenTarget((target) => setWanted(target === "new" ? COMPOSE : target)), []);
  const place = useCallback(
    (target: string | null) => {
      setWanted(target);
      navigate(agentHash(appId, target === null ? {} : { opens: [target] }));
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
                  <li key={row.conversation_id}>
                    <button
                      type="button"
                      onClick={() => place(row.conversation_id)}
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
        key={shown.row.conversation_id}
        agent={agent}
        member={member}
        conversationId={shown.row.conversation_id}
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
  <ChatApp open={init.open} appId={init.agentId} member={init.member} agents={agents} />
));
