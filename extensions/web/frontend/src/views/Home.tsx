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
import { Empty, Waiting } from "@/kernel/panel";
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

/** The picker's one list: the apps, named the way the workspace column names them. */
const APPS = "Apps";

/** The words on the menu the history is narrowed by — a ladder to run the rows in, and the surfaces
 *  the member keeps. */
const HISTORY_OPTIONS = "History options";
const SORT_BY = "Sort by";
const SHOW = "Show";

/** The name the connectors view stands under in the section registry, which the pane it is drawn in
 *  keys its one tab by. */
const CONNECTORS_SECTION = "connectors" as const;

/** What the connectors row says it is for, in the line every other row in the list carries. The
 *  screen states no purpose of its own — it is the portal's, not an app's — so the words are here,
 *  beside the list that draws them. */
const CONNECTORS_PURPOSE = "Connect the accounts your apps work in.";

/** One row of the picker's app list: the lane the press stands, and the marks and words the row is
 *  read by. An app names its own; the connectors screen is named here, because the portal draws it
 *  rather than an app the roster holds. */
type PickApp = {
  key: string;
  lane: string;
  label: string;
  purpose: string | null;
  mark: ReactNode;
};

/** The place the framed page inside a lane stands at. Home holds no place of its own past its
 *  track, and the frame re-sends its `init` whenever this changes — so it is one value rather
 *  than an empty record minted per render. */
const LANE_PLACE: WorkspacePlace = {};


/** The picker's one list. It takes the lane whole, and only the rows move — a heading that scrolled
 *  away would leave the member reading a list with no name on it. */
const PICK_SECTION = "flex min-h-0 flex-1 flex-col";

const PICK_LABEL =
  "m-0 flex h-(--size-row) shrink-0 items-center px-lg font-sans text-label font-medium text-ink-soft";

const PICK_ROWS = "min-h-0 flex-1 overflow-y-auto scrollbar-gutter-stable pb-md";

const PICK_EMPTY = "flex min-h-0 flex-1 flex-col";

/** The lane home opens on for a member holding no track: the chat app where the member types.
 *  A workspace holding no chat app opens on the picker — the one thing a member can act on where
 *  there is nothing to read. */
function defaultLanes(chatAgent: Agent | null): string[] {
  return chatAgent ? [mintHomeLane(chatAgent.id, [])] : [HOME_NEW_LANE];
}

/** The home screen: a track of open app instances, one lane each, in the order the address states
 *  them. Every lane holds what the app's own screen would show — its setup screen while the
 *  workspace is still wiring it, its page once built, its conversation where the app is the chat
 *  app — under the app's name, the way to its own screen, and the band the row is reordered by.
 *  An app may stand in more than one lane: each is its own instance, so two chat lanes hold two
 *  conversations.
 *
 *  The track is drawn over the pane rather than beside a body, because home has no body to draw:
 *  every lane on this screen is a lane, and `Pane`'s own — a reading lane in all but name —
 *  would stand an empty column of equal width beside them, so one open lane would take half the
 *  screen rather than the whole of it.
 *
 *  Standing here with no track in the address is a member arriving from somewhere else: the router
 *  hands back the row home was left holding, and where it held none the default set is written in
 *  the same place. It is written rather than merely drawn, because the address is what the rail
 *  reads the open lanes off, what a reorder writes back, and what closing a lane cuts from.
 *
 *  `seeking` is the lane the rail was last pressed for, brought into view by the track: the row may
 *  be wider than the screen, and the tile is how the member reaches a lane that scrolled off it.
 *  `onActive` is the answer travelling the other way — the lane the member is standing in, which the
 *  rail marks, since the tile a press lands on is not the only way a lane becomes the one they are
 *  in. */
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
  /* Home always stands a lane. A track of nothing is a screen with no act on it, and the address
     cannot state one either — an empty track writes no key, which is the same address a member
     arrives on, so the row the last close left would come back as the default set on the next
     render. The picker stands in its place instead: the one lane that says what to do next. */
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

/** The lane every kind of home lane is drawn as: a slot on the track, so the address states
 *  where it stands, the store holds the row for the tab's life, and the band is the handle a
 *  reorder is carried by.
 *
 *  Nothing is drawn to say so. A grip beside the name would be a mark that names nothing and a
 *  second thing to press that moves nothing, standing in the row of acts where every other glyph
 *  is a verb; the band already reads as this lane and the cursor over it already says which way it
 *  travels.
 *
 *  Closing writes the shortened row through the router, the way every other act on this screen
 *  does; the band's close control is the one way to shut a lane. The picker standing alone draws no way
 *  out: closing it would put it straight back, since home stands a lane whatever the member
 *  shuts. */
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

/** What a lane draws where there is nothing to put in it — a page this workspace has never built,
 *  or a lane naming an app it does not hold. The band above states which of the two it is, and the
 *  lane holds the surface the page will fill. */
function Blank() {
  return <div className="min-h-0 flex-1 bg-surface" />;
}

/** Where a pick lands: the chosen id takes the picking lane's place, unless it already stands —
 *  the same record twice is two hosts over one state, so the pick closes the picking lane
 *  instead. */
function taken(opens: string[], lane: string, id: string): string[] {
  return opens.includes(id)
    ? opens.filter((held) => held !== lane)
    : opens.map((held) => (held === lane ? id : held));
}

/** The act a chat-shaped lane turns its own history with: pressed, the lane reads as the list of
 *  this app's conversations, and a row picked off it takes the lane over. Held, it darkens to the
 *  page's own ink rather than taking a filled box: the band's acts are marks a glyph apart, and a
 *  square drawn behind one of them is wider than the space between them. */
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

/** A lane turned to its history by the act on its band: the same list the chat lane opens on — the
 *  runs the rows stand in and the narrowings behind their glyph — over the entry the next
 *  conversation starts in. One history is drawn one way, so the list a member reads from the band is
 *  the list they read on a new tab.
 *
 *  The entry is the chat lane's own shape — `Chat` holding the history where a transcript would
 *  stand — so a send that fails is read where it was said: the words stand in the log with the
 *  fault under them, never a box that cleared and shows nothing. It founds on a key of its own, the
 *  way the wizard does: the lane's chat box may be founding on the lane key while this stands, and
 *  two boxes on one key are one row and one draft, the first to found leaving the other unable to
 *  send. A send here founds a conversation and the lane becomes it, exactly as picking a row off
 *  the list does; the hand-off clears the founding key, so the record it leaves cannot open the old
 *  conversation under the next lane this app stands up.
 *
 *  The hand-off lands on the send's answer, and it writes the track as the address holds it then —
 *  the send window is long enough for the member to change the track, and a write off the sending
 *  render would revert what they did. A lane the member closed, and a member no longer on home,
 *  take no write at all: the conversation stands on the rail. */
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

/** The member's own history, drawn in the chat lane where a transcript would be until the chat holds
 *  a word: the conversations they have had stand over the entry the next one starts in, so opening a
 *  chat tab is both the way back to what they were doing and the way into what they say next.
 *
 *  The rows come off the rail — the one member-scoped listing every screen reads them from — and a
 *  press takes the lane over as that conversation's lane, so the member reads it where they were
 *  going to write.
 *
 *  The runs and the menu are the chat app's own: a ladder the rows run in, the surfaces the member
 *  keeps, and the source each row came in on beside its name. Both choices are held in this browser
 *  rather than in the address — a member who asked to see their terminal sessions asked about their
 *  own history, and a lane that forgot would ask them again on every tab they open.
 *
 *  It stands in the box's own column, at the box's own padding, so a row and the words the member is
 *  about to write start on one left edge. A list that took the whole pane would sit a hand's width
 *  outside the box under it, and the lane would read as two screens rather than one. */
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
            stamps
            onPick={(conversationId) =>
              onOpens(taken(opens, lane, homeConversationLane(conversationId)))
            }
          />
        </div>
      ) : rail.phase === "loading" ? (
        <Waiting />
      ) : (
        <Empty>{NO_HISTORY}</Empty>
      )}
    </div>
  );
}

/** The runs of a history, drawn the one way wherever a history is read: one section per run under
 *  the run's own name, and a row per conversation inside it that says its title, the source it came
 *  in on, and where it opens.
 *
 *  `stamps` is whether the rows carry the moment their conversation last moved. A lane wide enough
 *  for the column states it; a lane that spends its width on the names asks for the same runs
 *  without it, so the two lists are one drawing rather than two that drift.
 *
 *  `headings` is whether the run names are drawn. A run the rows are still ordered by reads as one
 *  list without its name where the name is the date the reader can see for themselves, and the runs
 *  stay sections either way, so turning the names off moves no row.
 *
 *  Sections are keyed by the run's name: the runs are named off one `Map` of labels, so no two
 *  share one. */
function HistoryRuns({
  runs,
  onPick,
  stamps,
  headings = true,
}: {
  runs: ChatRun[];
  onPick: (conversationId: string) => void;
  stamps?: boolean;
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
              /* Where the conversation came in — the room the surface named, else the member's word
                 for the surface. A portal chat states none: the history is read in the portal, so a
                 source on every row would name where the member already is. */
              note={isPortalChat(row.surface) ? undefined : origin(row)}
              when={stamps ? row.last_at : undefined}
              onPress={() => onPick(row.conversation_id)}
            />
          ))}
        </section>
      ))}
    </>
  );
}

/** The narrowings behind one glyph, the way every other listing in the portal draws them. A ladder
 *  is a pick between orders and shuts the menu; a surface is a choice turned on and off and leaves
 *  it standing, so a member names both in one visit.
 *
 *  Every list drawn off the rail offers the same menu, so the member narrows their history the same
 *  way wherever they read it. */
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

/** An app's lane: its setup screen while the workspace is still wiring it, its page once one is
 *  built, and the surface that page will fill until then. */
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

/** The connectors lane: the screen the portal draws itself, standing in a lane the way an app's own
 *  page does. Its place is held here rather than in the address — the track carries the lanes and
 *  not what stands inside one, so a filter or an opened row lives as long as the lane. */
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

/** The chat app's lane: a conversation with the workspace, held on the lane's own key. Two chat
 *  lanes are two conversations — the key is the lane, so neither the draft nor the transcript of
 *  one reaches the other — and the send that founds one keeps it here rather than handing the
 *  member to the conversation screen, which would take the whole track away from them. The
 *  founding record the store leaves on that key is how the lane joins the conversation it
 *  opened. */
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

/** A conversation's lane: the transcript itself, keyed by the conversation rather than by the lane,
 *  so a conversation read here and on its own screen is one chat. The band names it the way the rail
 *  does, and the act beside it turns the lane to this app's history, off which another conversation
 *  takes the lane over.
 *
 *  The rail resolves it, because the rail is the list the lane was picked off. A conversation the
 *  rail no longer carries, or one whose app has left the roster, is a lane with nothing to draw: it
 *  says so under the name the address holds rather than standing an empty transcript. */
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
            {resolving ? <Waiting /> : <Empty>{NO_CONVERSATION}</Empty>}
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

/** The lane a member opened to pick what stands here, standing where the pick will. It holds the
 *  two lists a workspace is opened from: every app it draws, each by its mark, its name and what it
 *  is for; and under them the conversations the member has had, in the order the rail holds them —
 *  one list, one order, wherever they are read.
 *
 *  The history is the chat lane's own list, drawn by the same `HistoryRuns`: the surfaces the member
 *  put away stay put away and the ladder still orders the rows. The runs are named here only under
 *  the app ladder — a member who ordered their history by app asked which app each run is, and a
 *  lane that grouped the rows and named no group leaves the grouping unexplained. Under recency the
 *  picker stays one flat list: the date over every few rows in a lane this narrow costs more of it
 *  than it names. The rows carry no stamp either way, because the picker draws no date column.
 *
 *  Picking takes this lane's place in the row rather than opening a fifth beside it, so the pick
 *  lands where the member asked for it and the cap is not reached by a lane that names nothing. A
 *  conversation already standing is picked by closing the picker instead: the lane is the
 *  conversation, and the same id twice is two hosts over one transcript. */
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
  /* The apps this workspace draws, and under them the connectors screen — the portal draws that one
     itself, and a member reaches it the way they reach an app, since a lane is a lane whoever draws
     what stands in it. */
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
                <Empty>{rail.phase === "loading" ? <Waiting /> : NO_CHATS}</Empty>
              </div>
            )}
          </section>
        </div>
      }
    />
  );
}
