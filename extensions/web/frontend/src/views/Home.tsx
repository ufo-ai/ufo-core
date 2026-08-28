import { IconChevronRight, IconGripVertical, IconHistory } from "@tabler/icons-react";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { PressRow } from "@/components/ui/pressrow";
import { Empty, Waiting } from "@/kernel/panel";
import { SlotTrack, useSlot } from "@/kernel/slots";
import { isPortalChat, surfaceWord } from "@/lib/audience";
import { AgentIcon } from "@/lib/agentIcon";
import { cn } from "@/lib/cn";
import { agentName } from "@/lib/agentName";
import { clearChat, useChat } from "@/lib/chatStore";
import { CHAT_SURFACE } from "@/lib/mainAgent";
import { day } from "@/lib/moments";
import type { ChatRow } from "@/lib/rail";
import { seekChat, useRail } from "@/lib/railStore";
import { placeHome } from "@/lib/router";
import {
  HOME_MAX_LANES,
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
import type { Agent, Member } from "@/lib/types";

const NEW_TAB = "New tab";
const HISTORY = "History";
const NO_HISTORY = "No conversations yet.";

/** The heading the readable conversations a member's colleagues are in stand under, here and on the
 *  chat app's own screen: they are listed rather than hidden, and never mixed in with the member's
 *  own. */
const OTHER_MEMBERS = "Other members";
const NO_APP = "No such app";
const NO_APPS = "This workspace has no apps to open.";
const NO_CHATS = "No conversations to open.";
const NO_CONVERSATION = "This conversation is not available.";
const CONVERSATION = "Conversation";

/** The picker's two lists: the apps named the way the workspace column names them, and the
 *  conversations named as what they are here — the member's history. */
const APPS = "Apps";

/** The apps home stands on for a member who has arranged nothing, each named by the slug its
 *  extension ships it under rather than by its name — provisioning may suffix a name on a
 *  collision, and the slug is what the app is. The chat app follows them, so the lane a member
 *  types in stands where they last read. */
const HOME_DEFAULT_APPS = ["metrics", "meetings", "code"] as const;

/** The place the framed page inside a lane stands at. Home holds no place of its own past its
 *  track, and the frame re-sends its `init` whenever this changes — so it is one value rather
 *  than an empty record minted per render. */
const LANE_PLACE: WorkspacePlace = {};

const GRIP = "size-(--size-glyph) shrink-0 text-ink-faint";

/** One of the picker's two lists. Each takes half the lane and keeps it: a short roster of apps
 *  hands its half to nothing, so the conversations stand where the member last read them however
 *  many apps the workspace ships, and neither list has to be scrolled past to reach the other. Only
 *  the rows move — a heading that scrolled away would leave the member reading a list with no name
 *  on it. */
const PICK_SECTION = "flex min-h-0 basis-1/2 flex-col";

const PICK_LABEL =
  "m-0 flex h-(--size-row) shrink-0 items-center px-lg font-sans text-label font-medium text-ink-soft";

const PICK_ROWS = "min-h-0 flex-1 overflow-y-auto scrollbar-gutter-stable pb-md";

const PICK_EMPTY = "flex min-h-0 flex-1 flex-col";

/** The lanes home opens on a member holding no track: the workspace's shipped apps and its chat
 *  app after them, each as the instance the codec mints. An app this workspace does not hold
 *  stands no lane, and a workspace holding none of them opens on the picker — the one thing a
 *  member can act on where there is nothing to read. */
function defaultLanes(agents: Agent[], chatAgent: Agent | null): string[] {
  const shipped = [
    ...HOME_DEFAULT_APPS.map((app) => agents.find((agent) => agent.app === app)),
    chatAgent,
  ].filter((agent): agent is Agent => Boolean(agent));
  const opens: string[] = [];
  for (const agent of shipped) {
    if (!opens.includes(agent.id)) opens.push(mintHomeLane(agent.id, opens));
  }
  return opens.length ? opens : [HOME_NEW_LANE];
}

/** The home screen: a track of open app instances, one lane each, in the order the address states
 *  them. Every lane holds what the app's own screen would show — its setup screen while the
 *  workspace is still wiring it, its page once built, its conversation where the app is the chat
 *  app — under the app's name, the way to its own screen, and the grip the row is reordered by.
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
 *  reads the open lanes off, what a reorder writes back, and what closing a lane cuts from. */
export function Home({
  place,
  agents,
  member,
  mainAgent,
  onFounded,
  onActivity,
  onAgents,
}: {
  place: WorkspacePlace;
  agents: Agent[];
  member: Member;
  mainAgent: Agent | null;
  onFounded: (agent: Agent, conversationId: string, title: string) => void;
  onActivity: (conversationId: string) => void;
  onAgents: () => void;
}) {
  const chatAgent = agents.find((agent) => agent.app === CHAT_SURFACE) ?? mainAgent;
  const opens = place.opens;
  useEffect(() => {
    if (opens?.length) return;
    placeHome({ ...place, opens: defaultLanes(agents, chatAgent) }, "replace");
  }, [opens, place, agents, chatAgent]);
  /* An address naming more lanes than home stands is one nothing here minted. The lanes past the
     cap are not drawn, and every act writes the row that is. */
  const standing = useMemo(() => (opens ?? []).slice(0, HOME_MAX_LANES), [opens]);
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
      <SlotTrack over opens={standing} onMove={move}>
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
        onActivity={onActivity}
      />
    );
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
 *  The grip states that handle and stands only where a drag can land somewhere — the track makes
 *  no band a handle while one lane stands alone. It is a mark and not a control: the whole band is
 *  what the browser drags, and a button here would be a second thing to press that moved nothing.
 *  The acts beside it refuse a drag of their own, so the grip is the one place in the band that
 *  both says "carry me" and does.
 *
 *  Closing writes the shortened row through the router, the way every other act on this screen
 *  does, and the slot answers Escape with the same verb. The picker standing alone draws no way
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
  const grip =
    !fixed && opens.length > 1 ? <IconGripVertical className={GRIP} aria-hidden /> : null;
  return useSlot(node, {
    id: lane,
    title,
    glyph,
    tone,
    fixed,
    acts:
      grip || acts ? (
        <>
          {grip}
          {acts}
        </>
      ) : undefined,
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
 *  this app's conversations, and a row picked off it takes the lane over. */
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
      variant="quiet"
      size="icon"
      aria-label={HISTORY + " for " + agentName(agent.name)}
      aria-pressed={pressed}
      className={cn(pressed && "bg-fill")}
      onClick={onPress}
    >
      <IconHistory aria-hidden />
    </Button>
  );
}

/** The conversations a chat-shaped lane's history lists: this app's rows off the rail — the
 *  member's own conversations and the ones their audience grants make readable, which is the one
 *  member-scoped listing every screen reads them from. The app's own conversation index is not that
 *  listing: it answers every conversation the app holds, another member's private one among them
 *  for an admin, so a history drawn from it named colleagues the member never spoke with.
 *
 *  A colleague's row follows the member's own under one heading rather than standing among them, the
 *  way the chat app's own screen groups them. A row takes the lane over as that conversation's
 *  lane. */
function PastList({
  agent,
  lane,
  opens,
  onOpens,
}: {
  agent: Agent;
  lane: string;
  opens: string[];
  onOpens: (opens: string[]) => void;
}) {
  const rail = useRail();
  const rows = rail.rows.filter((row) => row.agent_id === agent.id);
  const theirs = rows.filter((row) => !row.mine);
  const drawn = (row: ChatRow) => (
    <PressRow
      key={row.conversation_id}
      line={row.title || agentName(row.agent_name)}
      note={isPortalChat(row.surface) ? undefined : surfaceWord(row.surface)}
      when={day(row.last_at) ?? undefined}
      onPress={() => onOpens(taken(opens, lane, homeConversationLane(row.conversation_id)))}
    />
  );
  return (
    <div className="min-h-0 flex-1 overflow-y-auto scrollbar-gutter-stable py-md">
      {rows.length ? (
        <div className="flex flex-col">
          {rows.filter((row) => row.mine).map(drawn)}
          {theirs.length ? (
            <>
              <h3 className={PICK_LABEL}>{OTHER_MEMBERS}</h3>
              {theirs.map(drawn)}
            </>
          ) : null}
        </div>
      ) : rail.phase === "loading" ? (
        <Waiting />
      ) : (
        <Empty>{NO_HISTORY}</Empty>
      )}
    </div>
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
            <AgentSetup agent={agent} admin={member.admin} onBuilt={onAgents} />
          </div>
        ) : home.state === "set" ? (
          <HomepageFrame
            agent={agent}
            member={member}
            url={home.url}
            generation={home.deploy_generation ?? 0}
            place={LANE_PLACE}
            onFounded={(speaking, conversationId, title) => {
              onFounded(speaking, conversationId, title);
              setSettles((count) => count + 1);
            }}
          />
        ) : (
          <Blank />
        )
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
          <PastList agent={agent} lane={lane} opens={opens} onOpens={onOpens} />
        ) : (
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            <Chat
              agent={agent}
              member={member}
              conversationId={founded?.conversationId ?? null}
              foundingKey={lane}
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
  onActivity,
}: {
  lane: string;
  conversationId: string;
  opens: string[];
  agents: Agent[];
  member: Member;
  onOpens: (opens: string[]) => void;
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
          <PastList agent={agent} lane={lane} opens={opens} onOpens={onOpens} />
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
  const offered = agents.filter((agent) => !agent.hidden);
  const chats = rail.rows;
  const pick = (id: string) => onOpens(taken(opens, lane, id));
  return (
    <Lane
      lane={lane}
      opens={opens}
      title={NEW_TAB}
      glyph={null}
      tone="bg-fill"
      fixed
      onOpens={onOpens}
      node={
        <div className="flex min-h-0 flex-1 flex-col">
          <section className={PICK_SECTION}>
            <h3 className={PICK_LABEL}>{APPS}</h3>
            {offered.length ? (
              <div className={cn(PICK_ROWS, "px-lg")}>
                <ul className="m-0 flex list-none flex-col gap-sm p-0">
                  {offered.map((agent) => (
                    <li key={agent.id}>
                      <button
                        type="button"
                        onClick={() => pick(mintHomeLane(agent.id, opens))}
                        className="group flex w-full items-center gap-2xl border-0 bg-transparent p-0 py-sm text-left text-inherit"
                      >
                        <span className="flex size-(--size-control) shrink-0 items-center justify-center rounded-avatar bg-fill-strong p-sm text-ink">
                          <AgentIcon name={agent.icon} />
                        </span>
                        <span className="flex min-w-0 flex-1 flex-col gap-lg">
                          <span className="[text-box:trim-both_cap_alphabetic] truncate text-label font-medium tracking-(--tracking-ui) text-ink">
                            {agentName(agent.name)}
                          </span>
                          {agent.purpose ? (
                            <span className="[text-box:trim-both_cap_alphabetic] truncate text-fine leading-(--leading-chrome) text-ink-soft">
                              {agent.purpose}
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
            ) : (
              <div className={PICK_EMPTY}>
                <Empty>{NO_APPS}</Empty>
              </div>
            )}
          </section>
          <section className={PICK_SECTION}>
            <h3 className={PICK_LABEL}>{HISTORY}</h3>
            {chats.length ? (
              <div className={cn(PICK_ROWS, "px-lg")}>
                <ul className="m-0 flex list-none flex-col gap-sm p-0">
                  {chats.map((row) => (
                    <li key={row.conversation_id}>
                      <button
                        type="button"
                        onClick={() => pick(homeConversationLane(row.conversation_id))}
                        className="group flex w-full items-center gap-2xl border-0 bg-transparent p-0 py-sm text-left text-inherit"
                      >
                        <span className="[text-box:trim-both_cap_alphabetic] min-w-0 flex-1 truncate text-label font-medium tracking-(--tracking-ui) text-ink">
                          {row.title || agentName(row.agent_name)}
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
