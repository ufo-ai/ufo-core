import {
  IconChevronRight,
  IconFilter2,
  IconHistory,
  IconPlug,
  IconPlus,
} from "@tabler/icons-react";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { PressRow } from "@/components/ui/pressrow";
import type { Placement } from "@/kernel/pager";
import { COLUMN } from "@/kernel/pane";
import { Empty, Loading } from "@/kernel/panel";
import { SlotTrack, opened, useSlot, type Seek } from "@/kernel/slots";
import { isPortalChat, origin } from "@/lib/audience";
import { AgentIcon } from "@/lib/agentIcon";
import { cn } from "@/lib/cn";
import { agentName } from "@/lib/agentName";
import { clearChat, useChat } from "@/lib/chatStore";
import { chatSurface } from "@/lib/mainAgent";
import {
  CHAT_LADDERS,
  CHAT_SHOWN_OPTIONS,
  chatRuns,
  holdChatHidden,
  holdChatLadder,
  useChatHidden,
  useChatLadder,
  type ChatLadder,
  type ChatRun,
} from "@/lib/rail";
import { seekChat, useRail } from "@/lib/railStore";
import { heldRoute, placeHome } from "@/lib/router";
import {
  HOME_CONNECTORS_LANE,
  HOME_NEW_LANE,
  homeConversationLane,
  homeLaneAgent,
  homeLaneConversation,
  mintHomeLane,
  type WorkspacePlace,
} from "@/lib/route";
import { AgentSetup } from "@/views/AgentSetup";
import { Chat } from "@/views/Chat";
import { HomepageFrame, useHomepage } from "@/views/HomepageFrame";
import { TabbedPane } from "@/views/TabbedPane";
import { CONNECTORS } from "@/views/registry";
import type { Agent, Member } from "@/lib/types";
import { GLYPH_STROKE } from "@/lib/glyph";

const NEW_TAB = "New tab";
const HISTORY = "History";
const NO_HISTORY = "No conversations yet.";

const NO_APP = "No such app";
const NO_CHATS = "No conversations to open.";
const NO_CONVERSATION = "This conversation is not available.";
const CONVERSATION = "Conversation";

const APPS = "Apps";

const HISTORY_OPTIONS = "History options";
const SORT_BY = "Sort by";
const SHOW = "Show";

const CONNECTORS_SECTION = "connectors" as const;

const CONNECTORS_PURPOSE = "Connect the accounts your apps work in.";

type PickApp = {
  key: string;
  lane: string;
  label: string;
  purpose: string | null;
  mark: ReactNode;
};

const LANE_PLACE: WorkspacePlace = {};


const PICK_SECTION = "flex min-h-0 flex-1 flex-col";

const PICK_LABEL =
  "m-0 flex h-(--size-row) shrink-0 items-center px-lg font-sans text-label font-medium text-ink-soft";

const PICK_ROWS = "min-h-0 flex-1 overflow-y-auto scrollbar-gutter-stable pb-md";

const PICK_EMPTY = "flex min-h-0 flex-1 flex-col";

function defaultLanes(chatAgent: Agent | null): string[] {
  return chatAgent ? [mintHomeLane(chatAgent.id, [])] : [HOME_NEW_LANE];
}

export function Home({
  place,
  agents,
  member,
  mainAgent,
  seeking,
  onActive,
  onFounded,
  onActivity,
  onAgents,
}: {
  place: WorkspacePlace;
  agents: Agent[];
  member: Member;
  mainAgent: Agent | null;
  seeking?: Seek;
  onActive: (lane: string | undefined) => void;
  onFounded: (agent: Agent, conversationId: string, title: string) => void;
  onActivity: (conversationId: string) => void;
  onAgents: () => void;
}) {
  const chatAgent = chatSurface(agents) ?? mainAgent;
  const opens = place.opens;
  useEffect(() => {
    if (opens?.length) return;
    placeHome({ ...place, opens: defaultLanes(chatAgent) }, "replace");
  }, [opens, place, agents, chatAgent]);
  const standing = useMemo(() => opens ?? [], [opens]);
  const move = useCallback(
    (next: string[]) =>
      placeHome({ ...place, opens: next.length ? next : [HOME_NEW_LANE] }, "replace"),
    [place],
  );
  return (
    <main className="relative flex min-h-0 min-w-0 flex-col">
      <SlotTrack over opens={standing} onMove={move} seek={seeking} onActive={onActive}>
        {standing.map((lane) => (
          <HomeLane
            key={lane}
            lane={lane}
            opens={standing}
            agents={agents}
            chatAgent={chatAgent}
            member={member}
            onOpens={move}
            onFounded={onFounded}
            onActivity={onActivity}
            onAgents={onAgents}
          />
        ))}
      </SlotTrack>
    </main>
  );
}

function HomeLane({
  lane,
  opens,
  agents,
  chatAgent,
  member,
  onOpens,
  onFounded,
  onActivity,
  onAgents,
}: {
  lane: string;
  opens: string[];
  agents: Agent[];
  chatAgent: Agent | null;
  member: Member;
  onOpens: (opens: string[]) => void;
  onFounded: (agent: Agent, conversationId: string, title: string) => void;
  onActivity: (conversationId: string) => void;
  onAgents: () => void;
}) {
  const conversationId = homeLaneConversation(lane);
  if (conversationId !== null) {
    return (
      <ConversationLane
        lane={lane}
        conversationId={conversationId}
        opens={opens}
        agents={agents}
        member={member}
        onOpens={onOpens}
        onFounded={onFounded}
        onActivity={onActivity}
      />
    );
  }
  if (lane === HOME_CONNECTORS_LANE) {
    return <ConnectorsLane lane={lane} opens={opens} onOpens={onOpens} />;
  }
  const agentId = homeLaneAgent(lane);
  if (agentId === null) {
    return <PickerLane lane={lane} opens={opens} agents={agents} onOpens={onOpens} />;
  }
  const agent = agents.find((entry) => entry.id === agentId);
  if (!agent) {
    return <Lane lane={lane} opens={opens} title={NO_APP} node={<Blank />} onOpens={onOpens} />;
  }
  if (agent.id === chatAgent?.id) {
    return (
      <ChatLane
        lane={lane}
        opens={opens}
        agent={agent}
        member={member}
        onOpens={onOpens}
        onFounded={onFounded}
        onActivity={onActivity}
      />
    );
  }
  return (
    <AppLane
      lane={lane}
      opens={opens}
      agent={agent}
      member={member}
      onOpens={onOpens}
      onFounded={onFounded}
      onAgents={onAgents}
    />
  );
}

function Lane({
  lane,
  opens,
  title,
  glyph,
  tone,
  fixed,
  acts,
  node,
  onOpens,
}: {
  lane: string;
  opens: string[];
  title: string;
  glyph?: ReactNode;
  tone?: string;
  fixed?: boolean;
  acts?: ReactNode;
  node: ReactNode;
  onOpens: (opens: string[]) => void;
}): ReactNode {
  return useSlot(node, {
    id: lane,
    title,
    glyph,
    tone,
    fixed,
    acts,
    onClose:
      opens.length > 1 || lane !== HOME_NEW_LANE
        ? () => onOpens(opens.filter((held) => held !== lane))
        : undefined,
  });
}

function Blank() {
  return <div className="min-h-0 flex-1 bg-surface" />;
}

function taken(opens: string[], lane: string, id: string): string[] {
  return opens.includes(id)
    ? opens.filter((held) => held !== lane)
    : opens.map((held) => (held === lane ? id : held));
}

function HistoryAct({
  agent,
  pressed,
  onPress,
}: {
  agent: Agent;
  pressed: boolean;
  onPress: () => void;
}) {
  return (
    <Button
      variant="mark"
      size="glyph"
      aria-label={HISTORY + " for " + agentName(agent.name)}
      aria-pressed={pressed}
      className={cn(pressed && "text-ink")}
      onClick={onPress}
    >
      <IconHistory aria-hidden stroke={GLYPH_STROKE} />
    </Button>
  );
}

function HistoryLane({
  agent,
  member,
  lane,
  opens,
  onOpens,
  onFounded,
}: {
  agent: Agent;
  member: Member;
  lane: string;
  opens: string[];
  onOpens: (opens: string[]) => void;
  onFounded: (agent: Agent, conversationId: string, title: string) => void;
}) {
  const foundingKey = "history:" + lane;
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <Chat
        agent={agent}
        member={member}
        conversationId={null}
        foundingKey={foundingKey}
        unsaid={<History lane={lane} opens={opens} onOpens={onOpens} />}
        onCreated={(conversationId, title) => {
          clearChat(foundingKey);
          onFounded(agent, conversationId, title);
          const seen = heldRoute();
          if (seen.kind !== "home") return;
          const held = seen.place.opens ?? [];
          if (!held.includes(lane)) return;
          clearChat(lane);
          placeHome(
            { ...seen.place, opens: taken(held, lane, homeConversationLane(conversationId)) },
            "replace",
          );
        }}
      />
    </div>
  );
}

function History({
  lane,
  opens,
  onOpens,
}: {
  lane: string;
  opens: string[];
  onOpens: (opens: string[]) => void;
}) {
  const rail = useRail();
  const ladder = useChatLadder();
  const hidden = useChatHidden();
  const runs = chatRuns(rail.rows, ladder, hidden, new Date());
  const openAtTop = useCallback(
    (history: HTMLDivElement | null) => history?.scrollTo({ top: 0, behavior: "auto" }),
    [],
  );
  return (
    <div className={cn(COLUMN, "flex min-h-0 flex-1 flex-col px-2xl py-md")}>
      <div className="flex shrink-0 items-center justify-between px-lg">
        <h3 className={cn(PICK_LABEL, "px-0")}>{HISTORY}</h3>
        <HistoryOptions ladder={ladder} hidden={hidden} />
      </div>
      {runs.length ? (
        <div ref={openAtTop} className="min-h-0 flex-1 overflow-y-auto scrollbar-gutter-stable">
          <HistoryRuns
            runs={runs}
            onPick={(conversationId) =>
              onOpens(taken(opens, lane, homeConversationLane(conversationId)))
            }
          />
        </div>
      ) : rail.phase === "loading" ? (
        <Loading />
      ) : (
        <Empty>{NO_HISTORY}</Empty>
      )}
    </div>
  );
}

function HistoryRuns({
  runs,
  onPick,
  headings = true,
}: {
  runs: ChatRun[];
  onPick: (conversationId: string) => void;
  headings?: boolean;
}) {
  return (
    <>
      {runs.map((run) => (
        <section key={run.label} className="flex flex-col">
          {headings ? <h4 className={PICK_LABEL}>{run.label}</h4> : null}
          {run.rows.map((row) => (
            <PressRow
              key={row.conversation_id}
              line={row.title || agentName(row.agent_name)}
              note={isPortalChat(row.surface) ? undefined : origin(row)}
              when={row.last_at}
              onPress={() => onPick(row.conversation_id)}
            />
          ))}
        </section>
      ))}
    </>
  );
}

function HistoryOptions({ ladder, hidden }: { ladder: ChatLadder; hidden: string[] }) {
  const narrowed = ladder !== "recency" || hidden.length > 0;
  const show = (surface: string, shown: boolean) =>
    holdChatHidden(shown ? hidden.filter((name) => name !== surface) : [...hidden, surface]);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="mark"
          size="glyph"
          aria-label={HISTORY_OPTIONS}
          className={cn(narrowed && "text-ink")}
        >
          <IconFilter2 aria-hidden stroke={GLYPH_STROKE} />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuLabel>{SORT_BY}</DropdownMenuLabel>
        <DropdownMenuRadioGroup
          value={ladder}
          onValueChange={(next) => holdChatLadder(next as ChatLadder)}
        >
          {CHAT_LADDERS.map((entry) => (
            <DropdownMenuRadioItem key={entry.value} value={entry.value}>
              {entry.label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
        <DropdownMenuSeparator />
        <DropdownMenuLabel>{SHOW}</DropdownMenuLabel>
        {CHAT_SHOWN_OPTIONS.map((option) => (
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
  );
}

function AppLane({
  lane,
  opens,
  agent,
  member,
  onOpens,
  onFounded,
  onAgents,
}: {
  lane: string;
  opens: string[];
  agent: Agent;
  member: Member;
  onOpens: (opens: string[]) => void;
  onFounded: (agent: Agent, conversationId: string, title: string) => void;
  onAgents: () => void;
}) {
  const [settles, setSettles] = useState(0);
  const home = useHomepage(agent, settles);
  return (
    <Lane
      lane={lane}
      opens={opens}
      title={agentName(agent.name)}
      glyph={<AgentIcon name={agent.icon} />}
      onOpens={onOpens}
      node={
        agent.stands_on_setup === true ? (
          <div className="min-h-0 flex-1 overflow-y-auto p-2xl">
            <AgentSetup agent={agent} onBuilt={onAgents} />
          </div>
        ) : home.state === "set" ? (
          <HomepageFrame
            agent={agent}
            member={member}
            url={home.url}
            generation={home.deploy_generation ?? 0}
            place={LANE_PLACE}
            banded
            onFounded={(speaking, conversationId, title) => {
              onFounded(speaking, conversationId, title);
              setSettles((count) => count + 1);
            }}
            onConversation={(conversationId) =>
              onOpens(opened(opens, homeConversationLane(conversationId), lane))
            }
          />
        ) : (
          <Blank />
        )
      }
    />
  );
}

function ConnectorsLane({
  lane,
  opens,
  onOpens,
}: {
  lane: string;
  opens: string[];
  onOpens: (opens: string[]) => void;
}) {
  const [place, setPlace] = useState<Placement>({});
  return (
    <Lane
      lane={lane}
      opens={opens}
      title={CONNECTORS.label}
      glyph={<IconPlug aria-hidden />}
      onOpens={onOpens}
      node={
        <TabbedPane
          group="section"
          tabs={[CONNECTORS_SECTION]}
          views={{ [CONNECTORS_SECTION]: CONNECTORS }}
          view={CONNECTORS_SECTION}
          banded
          place={place}
          onPlace={(_view, next) => setPlace(next)}
        />
      }
    />
  );
}

function ChatLane({
  lane,
  opens,
  agent,
  member,
  onOpens,
  onFounded,
  onActivity,
}: {
  lane: string;
  opens: string[];
  agent: Agent;
  member: Member;
  onOpens: (opens: string[]) => void;
  onFounded: (agent: Agent, conversationId: string, title: string) => void;
  onActivity: (conversationId: string) => void;
}) {
  const founded = useChat(lane).founded;
  const [history, setHistory] = useState(false);
  const shut = (next: string[]) => {
    if (!next.includes(lane)) clearChat(lane);
    onOpens(next);
  };
  return (
    <Lane
      lane={lane}
      opens={opens}
      title={history ? HISTORY : agentName(agent.name)}
      glyph={<AgentIcon name={agent.icon} />}
      acts={
        <HistoryAct agent={agent} pressed={history} onPress={() => setHistory((held) => !held)} />
      }
      onOpens={shut}
      node={
        history ? (
          <HistoryLane
            agent={agent}
            member={member}
            lane={lane}
            opens={opens}
            onOpens={shut}
            onFounded={onFounded}
          />
        ) : (
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            <Chat
              agent={agent}
              member={member}
              conversationId={founded?.conversationId ?? null}
              foundingKey={lane}
              unsaid={<History lane={lane} opens={opens} onOpens={onOpens} />}
              onCreated={(conversationId, title) => onFounded(agent, conversationId, title)}
              onActivity={onActivity}
            />
          </div>
        )
      }
    />
  );
}

function ConversationLane({
  lane,
  conversationId,
  opens,
  agents,
  member,
  onOpens,
  onFounded,
  onActivity,
}: {
  lane: string;
  conversationId: string;
  opens: string[];
  agents: Agent[];
  member: Member;
  onOpens: (opens: string[]) => void;
  onFounded: (agent: Agent, conversationId: string, title: string) => void;
  onActivity: (conversationId: string) => void;
}) {
  const rail = useRail();
  const [history, setHistory] = useState(false);
  const row = rail.rows.find((held) => held.conversation_id === conversationId);
  const linked = rail.linked[conversationId];
  const agentId = row?.agent_id ?? linked?.agent?.id ?? null;
  const agent = agents.find((entry) => entry.id === agentId);
  const sought = rail.sought[conversationId];
  useEffect(() => {
    if (!row && !linked && rail.phase === "ready" && sought === undefined) {
      seekChat(conversationId);
    }
  }, [row, linked, rail.phase, sought, conversationId]);
  if (!agent) {
    const resolving = rail.phase !== "ready" || (!row && !linked && sought === undefined);
    return (
      <Lane
        lane={lane}
        opens={opens}
        title={CONVERSATION}
        onOpens={onOpens}
        node={
          <div className="flex min-h-0 flex-1 flex-col bg-surface">
            {resolving ? <Loading /> : <Empty>{NO_CONVERSATION}</Empty>}
          </div>
        }
      />
    );
  }
  const title = row?.title || linked?.description || agentName(agent.name);
  return (
    <Lane
      lane={lane}
      opens={opens}
      title={history ? HISTORY : title}
      glyph={<AgentIcon name={agent.icon} />}
      acts={
        <HistoryAct agent={agent} pressed={history} onPress={() => setHistory((held) => !held)} />
      }
      onOpens={onOpens}
      node={
        history ? (
          <HistoryLane
            agent={agent}
            member={member}
            lane={lane}
            opens={opens}
            onOpens={onOpens}
            onFounded={onFounded}
          />
        ) : (
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            <Chat
              agent={agent}
              member={member}
              conversationId={conversationId}
              onActivity={onActivity}
            />
          </div>
        )
      }
    />
  );
}

function PickerLane({
  lane,
  opens,
  agents,
  onOpens,
}: {
  lane: string;
  opens: string[];
  agents: Agent[];
  onOpens: (opens: string[]) => void;
}) {
  const rail = useRail();
  const ladder = useChatLadder();
  const hidden = useChatHidden();
  const runs = chatRuns(rail.rows, ladder, hidden, new Date());
  const pick = (id: string) => onOpens(taken(opens, lane, id));
  const offered: PickApp[] = [
    ...agents
      .filter((agent) => !agent.hidden)
      .map((agent) => ({
        key: agent.id,
        lane: mintHomeLane(agent.id, opens),
        label: agentName(agent.name),
        purpose: agent.purpose ?? null,
        mark: <AgentIcon name={agent.icon} />,
      })),
    {
      key: HOME_CONNECTORS_LANE,
      lane: HOME_CONNECTORS_LANE,
      label: CONNECTORS.label,
      purpose: CONNECTORS_PURPOSE,
      mark: <IconPlug aria-hidden />,
    },
  ];
  return (
    <Lane
      lane={lane}
      opens={opens}
      title={NEW_TAB}
      glyph={<IconPlus aria-hidden />}
      tone="bg-fill"
      fixed
      onOpens={onOpens}
      node={
        <div className="flex min-h-0 flex-1 flex-col">
          <section className={PICK_SECTION}>
            <h3 className={PICK_LABEL}>{APPS}</h3>
            <div className={cn(PICK_ROWS, "px-lg")}>
              <ul className="m-0 flex list-none flex-col gap-sm p-0">
                {offered.map((app) => (
                  <li key={app.key}>
                    <button
                      type="button"
                      onClick={() => pick(app.lane)}
                      className="group flex w-full items-center gap-2xl border-0 bg-transparent p-0 py-sm text-left text-inherit"
                    >
                      <span className="flex size-(--size-control) shrink-0 items-center justify-center rounded-avatar bg-fill-strong p-sm text-ink">
                        {app.mark}
                      </span>
                      <span className="flex min-w-0 flex-1 flex-col gap-lg">
                        <span className="[text-box:trim-both_cap_alphabetic] truncate text-label font-medium tracking-(--tracking-ui) text-ink">
                          {app.label}
                        </span>
                        {app.purpose ? (
                          <span className="[text-box:trim-both_cap_alphabetic] truncate text-fine leading-(--leading-chrome) text-ink-soft">
                            {app.purpose}
                          </span>
                        ) : null}
                      </span>
                      <IconChevronRight
                        className="size-(--size-glyph) shrink-0 text-ink-soft opacity-0 transition-opacity duration-100 ease-control group-hover:opacity-100 group-focus-visible:opacity-100"
                        aria-hidden
                      />
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          </section>
          <section className={PICK_SECTION}>
            <div className="flex shrink-0 items-center justify-between px-lg">
              <h3 className={cn(PICK_LABEL, "px-0")}>{HISTORY}</h3>
              <HistoryOptions ladder={ladder} hidden={hidden} />
            </div>
            {runs.length ? (
              <div className={PICK_ROWS}>
                <HistoryRuns
                  runs={runs}
                  headings={ladder === "app"}
                  onPick={(conversationId) => pick(homeConversationLane(conversationId))}
                />
              </div>
            ) : (
              <div className={PICK_EMPTY}>
                <Empty>{rail.phase === "loading" ? <Loading /> : NO_CHATS}</Empty>
              </div>
            )}
          </section>
        </div>
      }
    />
  );
}
