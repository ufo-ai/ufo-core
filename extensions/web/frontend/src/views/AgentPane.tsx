import { useCallback, useEffect, useRef, useState } from "react";
import {
  IconClipboardCheck,
  IconDots,
  IconHistory,
  IconLayoutSidebarRight,
  IconMessage,
  IconPlug,
  IconPlus,
  IconSettings,
} from "@tabler/icons-react";

import { attachBridge, type BridgeHandle } from "@/lib/bridge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { PressRow } from "@/components/ui/pressrow";
import { Header } from "@/kernel/pane";
import { Panel, PanelEmpty, usePanelRead } from "@/kernel/panel";
import { useSlot } from "@/kernel/slots";
import { isPortalChat, surfaceWord, useViewer } from "@/lib/audience";
import { agentName } from "@/lib/agentName";
import { CHAT_SURFACE, useAgents } from "@/lib/mainAgent";
import { cn } from "@/lib/cn";
import { day } from "@/lib/moments";
import type { ChatRow } from "@/lib/rail";
import type { SetupState } from "@/views/AgentSetup";
import { Chat } from "@/views/Chat";
import {
  ConversationDetail,
  Disclose,
  conversationTitle,
  subject,
} from "@/views/Conversations";
import {
  COMPOSE,
  agentSetupHash,
  mergePlace,
  serializePlace,
  type PlaceStep,
  type WorkspacePlace,
} from "@/lib/route";
import { navigate } from "@/lib/router";
import { agentCrumb } from "@/lib/title";
import type { Agent, Conversation, Homepage, Member } from "@/lib/types";

/** What the half is called before a conversation exists to name it, and the act that starts one.
 *  The act stands with the acts at the far end of the band, where every act on the whole surface
 *  stands. */
const NEW_CONVERSATION = "New conversation";
const NEW = "New";

/** The slot a conversation nobody has founded yet stands in. Every other slot on this screen is
 *  named by the conversation it holds; this one has no conversation to name it, and the send that
 *  founds one writes that conversation's id over it. */
const FRESH = "new";

/** What the lane's own list of the app's conversations is called, and what it says where the app
 *  has held none. The lane holds either a conversation or the list of them, so the band names
 *  whichever is standing and the way out shuts that one. */
const HISTORY = "History";
const NO_HISTORY = "No conversations yet.";
/** What the list says where the read stopped at its own bound. The read carries no cursor, so the
 *  list cannot page — and a list that ended in silence on a set the member cannot know is cut
 *  states a history the app does not have. Search is the way past the bound: it narrows the read
 *  rather than the page, so a conversation older than this list holds is still found. */
const HISTORY_BOUND = "The newest few. Search finds an older one.";

/** What an app's own dialog holds: the spec the member edits, the accounts the app reaches, and the
 *  tasks that run it on a clock. Three reads of one app, none of which heads a page of its own. The
 *  skills are the workspace's, so they stand on the workspace page and the spec states only whether
 *  this app loads them. The names live here because the band's menu is what picks between them. */
export const SETTINGS_TABS = ["settings", "connectors", "scheduled"] as const;
export type SettingsTab = (typeof SETTINGS_TABS)[number];
export const SETTINGS_TAB_LABELS: Record<SettingsTab, string> = {
  settings: "Settings",
  connectors: "Connectors",
  scheduled: "Scheduled",
};

/** The weight the menu's glyphs are drawn at: light enough beside 13px type that a row reads as its
 *  word with a mark beside it, rather than as an icon with a caption. */
const GLYPH_STROKE = 1.25;

/** The glyph each read is drawn with. The menu takes its names and its order from the tabs
 *  themselves, so a member picks the same word here that heads the panel they land on. */
const SETTINGS_TAB_GLYPHS: Record<SettingsTab, typeof IconSettings> = {
  settings: IconSettings,
  connectors: IconPlug,
  scheduled: IconClipboardCheck,
};

/** The three reads as a member picks between them. Both places that offer the pick stand this one
 *  list — the band's menu on the app screen, and the panel's own breadcrumb once it is open — so a
 *  read is named, marked and set the same way wherever it is chosen. */
export function SettingsTabItems({ onPick }: { onPick: (tab: SettingsTab) => void }) {
  return (
    <>
      {SETTINGS_TABS.map((tab) => {
        const Glyph = SETTINGS_TAB_GLYPHS[tab];
        return (
          <DropdownMenuItem
            key={tab}
            className="justify-start gap-md rounded-row text-label tracking-ui"
            onSelect={() => onPick(tab)}
          >
            <Glyph className="size-(--size-glyph) shrink-0" stroke={GLYPH_STROKE} aria-hidden />
            {SETTINGS_TAB_LABELS[tab]}
          </DropdownMenuItem>
        );
      })}
    </>
  );
}

/** The acts an app carries beyond its conversation, gathered under one glyph. Each opens the app's
 *  own dialog on the read it names, so all three are reached from the band the app heads rather
 *  than from a gear that names only one of them. */
function AppMenu({
  name,
  onPick,
  className,
}: {
  name: string;
  onPick: (tab: SettingsTab) => void;
  className?: string;
}) {
  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <Button variant="quiet" size="icon" aria-label={"Menu for " + name} className={className}>
          <IconDots aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-(--container-menu)">
        <SettingsTabItems onPick={onPick} />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** How long the arriving frame takes to fade over the one it replaces, and how long after that the
 *  replaced frame is kept mounted under it. The page it swaps in has already fired `load`, so the
 *  fade is the whole wait — nothing here delays the swap past the paint it exists to smooth. */
const FRAME_SWAP_MS = 200;
const HOMEPAGE_POLL_MS = 30_000;

const HOMEPAGE_SANDBOX =
  "allow-scripts allow-same-origin allow-forms allow-popups allow-modals allow-downloads allow-pointer-lock";

/** One mounted copy of the homepage frame. A redeploy or ended session remounts the page at the
 *  same URL, and a frame torn down the instant its successor mounts leaves the member watching
 *  the successor's blank document paint — so the standing copy holds the screen until the
 *  arriving one has loaded, and the two cross-fade. `key` is the URL, deploy generation, and
 *  session refresh together, the identity the pane already remounts on. */
type HeldFrame = { key: string; url: string; loaded: boolean };

export type AgentPaneProps = {
  agent: Agent;
  member: Member;
  /** Every conversation the portal can carry on, as the rail already holds them, or null until the
   *  rail has answered. The chat transport answers for a conversation the web surface founded and
   *  for no other, so the rail — not the app's conversation index, which lists every surface the
   *  app has ever spoken on — is what says which ones this half can open. Null is not the empty
   *  list: a conversation the member owns would otherwise read as one they may not continue for as
   *  long as the rail takes to arrive, and forever if it never does. */
  chats: ChatRow[] | null;
  place: WorkspacePlace;
  onSettings: (tab: SettingsTab) => void;
  /** The founded conversation, told to the shell so the rail carries the row this half is now
   *  holding — this half reads the rail to know which conversations it can carry on, so a chat the
   *  rail has not heard of is one the half would slide off the moment anything re-read. */
  onCreated: (conversationId: string, title: string) => void;
  /** A conversation the framed page's own send founded, with the agent it ran under — the page
   *  chats across agents, so the pane's own agent cannot stand in. */
  onFounded: (agent: Agent, conversationId: string, title: string) => void;
  onPlace: (place: WorkspacePlace, step: PlaceStep) => void;
};

/** The apps screen's pane: the app's homepage whole, headed by the app's name with the way out to
 *  the page and the settings act beside it. Opening an app is opening what it built — the page IS
 *  the app to the member reading it — so nothing shares the width with it.
 *
 *  An app that has built no page draws its conversation instead: arriving opens the one that moved
 *  last, so the screen lands on the work rather than on a list of it, and an app nobody has spoken
 *  to opens the composer, because the first thing a member does with a new app is talk to it.
 *
 *  Beside a page stands one conversation: the app's editing chat, the newest the member directs
 *  the app in, opened by the band's toggle. The sidebar is the list of the rest — any of them
 *  opens here by its own address. */
export function AgentPane({
  agent,
  member,
  chats,
  place,
  onSettings,
  onCreated,
  onFounded,
  onPlace,
}: AgentPaneProps) {
  const [settles, setSettles] = useState(0);
  const [frameRefresh, setFrameRefresh] = useState(0);
  const sessionEnded = useRef(false);
  const refreshEndedSession = useCallback(() => {
    if (!sessionEnded.current) return;
    sessionEnded.current = false;
    setFrameRefresh((value) => value + 1);
  }, []);
  const viewer = useViewer();
  const agents = useAgents();
  const target = place.opens?.[0];
  const boot: Homepage = agent.homepage ?? { state: "none" };
  const [homepagePolling, setHomepagePolling] = useState(false);
  useEffect(() => {
    const start = window.setTimeout(() => setHomepagePolling(true), HOMEPAGE_POLL_MS);
    return () => window.clearTimeout(start);
  }, []);
  const site = usePanelRead<Homepage>(
    homepagePolling ? "/agents/" + agent.id + "/homepage" : null,
    settles,
    HOMEPAGE_POLL_MS,
  );
  const home: Homepage = site.phase === "ready" ? site.payload : boot;
  // An app the workspace has not finished wiring stands on its setup screen instead of here. With
  // no account there is nothing real for its page to draw, and the alternative — sample rows in
  // place of records — shows a member someone else's app and calls it theirs. The screen is an
  // address rather than a band over this one, so it is linkable, and the acts it carries are the
  // portal's own: a framed page can start neither an admin's workspace install nor a model turn.
  // A shipped app the workspace has never built stands on its setup screen, and the moment it has
  // built one it stands on that page for good. Built is the line, not wired: an app builds a
  // thinner page from fewer sources, and a member who wants to see it before every todo is settled
  // gets to.
  //
  // Only an app an extension shipped is sent there. The main agent and an agent a member built
  // have no bound site and are never meant to have one — they draw their conversation column, and
  // a gate that read "no site" as "never built" made that column unreachable at its own address.
  //
  // And only an answer that says so in as many words sends the member away: a read that failed, or
  // one whose payload states nothing, leaves them on the page they asked for.
  const shipped = Boolean(agent.app);
  const setup = usePanelRead<SetupState>(
    shipped ? "/agents/" + agent.id + "/setup" : null,
    settles,
  );
  // An app is sent to its setup screen only while it has setup to do: it declares something, and
  // the workspace has not built it a page yet. An app that declares nothing has nothing that screen
  // could list — chat, radar, tasks, wiki and artifacts declare none — so it stands on its page.
  //
  // The move replaces rather than pushes. A pushed entry sends Back to the app address, which
  // mounts the pane, reads the same answer and pushes the setup screen over it again, so the member
  // can never step back past the app.
  const owed =
    setup.phase === "ready" &&
    Boolean(
      setup.payload.connectors?.length
        || setup.payload.credentials?.length
        || setup.payload.standing?.length,
    );
  const unbuilt = shipped && owed && setup.payload.own_page === false;
  useEffect(() => {
    if (unbuilt) navigate(agentSetupHash(agent.id), "replace");
  }, [unbuilt, agent.id]);
  // Two reads, each authoritative for a different question. The index says which conversations the
  // app has at all — every surface it has ever spoken on — and which external ones accept comments.
  // The rail says which portal and extension conversations are the app's directive chats.
  //
  // The index is read only when a conversation is on screen: one the address opens, or — for an
  // agent whose page is not set — the editing conversation the pane stands on by default, since
  // there the conversation is the screen. An app showing its own set page with nothing open needs
  // none of it, so switching between apps does not pull each one's conversation history; the chat
  // toggle resumes the editing conversation from the rail the shell already holds.
  const listed = usePanelRead<{ conversations: Conversation[]; more: boolean }>(
    target !== COMPOSE && (target !== undefined || home.state !== "set")
      ? "/agents/" + agent.id + "/conversations"
      : null,
    settles,
  );
  const rows = listed.phase === "ready" ? (listed.payload.conversations ?? []) : [];
  // The app's directive conversations as the rail carries them, and the one the chat toggle
  // opens: the newest of them, because an app has one editing conversation — the chat the member
  // directs the app in — and the newest row is where that direction last happened. An app nobody
  // has directed yet has none, and the toggle opens the composer; the send that founds one makes
  // it the editing chat from then on.
  const directives =
    chats === null
      ? null
      : chats.filter((row) => row.agent_id === agent.id && row.mine && isPortalChat(row.surface));
  const live = directives === null ? null : new Set(directives.map((row) => row.conversation_id));
  const editing =
    directives === null
      ? null
      : directives.reduce<ChatRow | null>(
          (newest, row) => (newest === null || row.last_at > newest.last_at ? row : newest),
          null,
        );
  // What the address opens, split by what it names. The fresh sentinel and the app's own
  // conversations — the chats that direct this app — are the pane's to hold: the right-side chat,
  // a slot in this screen's track beside the page. Any other target — a run, a record, another
  // agent's conversation — is the page's own, handed to the frame in `init` and opening nothing
  // portal-side: the page is the screen that knows how to stand on it. An address naming nothing
  // opens the newest conversation the member can speak in, so arriving lands on the work rather
  // than on a list of it, and on an app they have only ever read, on the composer.
  const composeFallback = target === COMPOSE && home.state !== "set";
  // The rail's own row for a target the index answer does not carry. The two reads are bounded
  // differently — the index answers one page of 100 rows over every surface the app has ever spoken
  // on, machine lanes included, while the rail bounds the member's own chats on their own — so an
  // app holding 100 rows newer than the member's chat with it keeps that chat on the rail and
  // outside the index's page. The rail is what says which conversations this half can open, so a
  // row it carries is one the half holds, whether the index answered it or not.
  const railHeld =
    target !== undefined && !rows.some((entry) => entry.id === target)
      ? (directives?.find((row) => row.conversation_id === target) ?? null)
      : null;
  const conversational =
    composeFallback ||
    (target !== undefined &&
      (target === FRESH || rows.some((entry) => entry.id === target) || railHeld !== null));
  const held = composeFallback ? FRESH : conversational ? target : undefined;
  const wanted = held === FRESH;
  const named =
    held !== undefined && !wanted ? (rows.find((entry) => entry.id === held) ?? null) : null;
  // Acknowledging is recorded, not reflected: the index answers `readable` from audience membership
  // alone, so a conversation this member has just opened still arrives false and would be handed
  // back its own gate — and every press would write another audit row for a disclosure already
  // made. What the member did in this pane is held here, the way a permalink's pane holds it.
  const [disclosed, setDisclosed] = useState<string | null>(null);
  const start = () => onPlace({ ...place, opens: [FRESH] }, "push");
  // Which lane the list is standing over, rather than whether it is open at all. The lane is named
  // by the conversation it holds, so opening one from the list — or shutting the lane, or starting
  // a conversation — names a different lane and puts the list away with nothing having to remember
  // to. A flag would survive all three and draw the list over the conversation the member just
  // opened.
  const [listing, setListing] = useState<string | null>(null);
  const lane = held ?? FRESH;
  const history = held !== undefined && listing === lane;
  // The fallback to the newest conversation the member can speak in stands for an address that
  // names none; a target the rail holds is already named, so answering it with another row would
  // open a place the address never asked for.
  const opened =
    wanted || railHeld !== null
      ? null
      : (named ??
        (live === null
          ? null
          : (rows.find((entry) => entry.id === editing?.conversation_id) ??
            rows.find((entry) => live.has(entry.id)) ??
            null)));
  // What the half is drawing, named rather than spelled inline: four states read as a chain of
  // conditions no one can follow. Nothing is drawn while the reads that decide are still in
  // flight — a composer put up for that frame is one the member could type into, and the words
  // would be founded on a conversation the next answer replaces. A target the rail already names
  // waits for neither read: the row is the whole answer, and the index will not carry it.
  const settling =
    opened === null &&
    !wanted &&
    railHeld === null &&
    (live === null || listed.phase === "loading");
  // A conversation shared with nobody this member belongs to is not one an acknowledgement can
  // open: the index says so on the row itself, and the intent would refuse. The half says that
  // rather than offering an act that cannot be taken.
  const walled = opened !== null && !opened.readable && !opened.disclosable;
  const gated = opened !== null && !walled && !opened.readable && disclosed !== opened.id;
  const reading =
    opened !== null &&
    !walled &&
    !gated &&
    live !== null &&
    !live.has(opened.id) &&
    !opened.commentable;

  const url = home.state === "set" ? home.url : null;
  const generation = home.state === "set" ? (home.deploy_generation ?? 0) : 0;
  useEffect(() => {
    window.addEventListener("focus", refreshEndedSession);
    return () => window.removeEventListener("focus", refreshEndedSession);
  }, [refreshEndedSession]);
  // The half stands for a homepage that exists; an app with none draws one column, because a column
  // whose only content is the sentence that it is empty takes half the screen to say what the app
  // having no homepage already says.
  const beside = url !== null;
  // A target the pane cannot hold is the page's to stand on — but an app with no page has nowhere
  // to hand it. A conversation the rail knows is drawn here under its own agent, so a rail click
  // works in the window before the app's page exists; anything else states the miss, because
  // answering with some other conversation would open a place the link never named.
  const railRow =
    target !== undefined && !conversational && !beside
      ? ((chats ?? []).find(
          (row) => row.conversation_id === target && row.mine && isPortalChat(row.surface),
        ) ?? null)
      : null;
  const railAgent = railRow ? (agents.find((entry) => entry.id === railRow.agent_id) ?? null) : null;
  const missing =
    target !== undefined &&
    !conversational &&
    !beside &&
    railAgent === null &&
    listed.phase === "ready";
  // The bridge is how the framed page reads the member's data and drives navigation; it is bound to
  // the live frame and rebound when a redeploy remounts it under a new key, so each set of bytes
  // talks to exactly one listener. The page stands at this pane's own place, less the track the pane
  // holds as a slot itself — one meaning per channel, so nothing is opened twice. The place rides
  // `init` on a fresh frame and the bridge's own `place` message while the frame stands, because a
  // rail click lands on a page that already booted. It crosses whole: a frame handed one key of a
  // place could only stand the screen the address names by guessing the rest.
  //
  // The page's own step of the trail crosses with it, as the crumb its band draws over a name the
  // shell never read. The app is where the page stands, so this is that step and the shell derives
  // no second answer to it.
  const frameRef = useRef<HTMLIFrameElement>(null);
  const bridgeRef = useRef<BridgeHandle | null>(null);
  const framed = mergePlace(place, conversational ? { opens: undefined } : {});
  const framedRef = useRef(framed);
  framedRef.current = framed;
  const framedAt = serializePlace(framed);
  // The frames the pane is holding: the page showing, and — through a redeploy — the copy arriving
  // under the new generation. Reconciled in render rather than an effect so the arriving frame
  // mounts in the same commit that moves the bridge's dependencies, which is what points `frameRef`
  // at it before the bridge attaches. At most two stand at once: the last loaded copy and the one
  // arriving, so a redeploy racing another drops the copy that never showed.
  const [frames, setFrames] = useState<HeldFrame[]>([]);
  if (url === null) {
    if (frames.length > 0) setFrames([]);
  } else {
    const frameKey = url + ":" + generation + ":" + frameRefresh;
    if (frames.at(-1)?.key !== frameKey) {
      setFrames([
        ...frames.filter((frame) => frame.loaded).slice(-1),
        { key: frameKey, url, loaded: false },
      ]);
    }
  }
  // A load promotes only the newest frame: one from a copy already being replaced would fade in
  // bytes the next deploy has superseded. The replaced frame unmounts once the fade is over, and a
  // frame that never loads leaves it standing — the member keeps the page they had.
  const landed = (key: string) => {
    setFrames((held) =>
      held.at(-1)?.key === key
        ? held.map((frame) => (frame.key === key ? { ...frame, loaded: true } : frame))
        : held,
    );
    window.setTimeout(() => {
      setFrames((held) =>
        held.at(-1)?.key === key && held.at(-1)?.loaded ? held.slice(-1) : held,
      );
    }, FRAME_SWAP_MS);
  };
  const foundedRef = useRef<(agentId: string, conversationId: string, title: string) => void>(
    () => {},
  );
  foundedRef.current = (agentId, conversationId, title) => {
    const speaking = agents.find((entry) => entry.id === agentId);
    if (speaking) {
      onFounded(speaking, conversationId, title);
      setSettles((count) => count + 1);
    }
  };
  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    const handle = attachBridge({
      iframe: frame,
      member,
      agents,
      agentId: agent.id,
      place: framedRef.current,
      crumb: agentCrumb(agent),
      chatSurface: agent.app === CHAT_SURFACE,
      onCreated: (agentId, conversationId, title) =>
        foundedRef.current(agentId, conversationId, title),
      onSessionEnded: () => {
        sessionEnded.current = true;
        if (document.hasFocus()) refreshEndedSession();
      },
    });
    bridgeRef.current = handle;
    return () => {
      bridgeRef.current = null;
      handle.detach();
    };
  }, [
    member,
    agents,
    agent.id,
    agent.app,
    url,
    generation,
    frameRefresh,
    refreshEndedSession,
  ]);
  useEffect(() => {
    bridgeRef.current?.place(framedRef.current);
  }, [framedAt]);

  /** The conversation, whole. Its own band is drawn where the conversation is the screen; standing
   *  in a lane, the lane's header already states the name and draws the way out, and a second band
   *  under it would state both a second time. */
  const conversation = (
    <section aria-label={agentName(agent.name)} className="flex min-h-0 min-w-0 flex-1 flex-col">
      {beside ? null : (
        <Header
          heading={2}
          title={
            opened
              ? opened.commentable
                ? conversationTitle(opened, viewer)
                : subject(opened, viewer)
              : (railHeld?.title ?? NEW_CONVERSATION)
          }
          acts={
            <>
              <Button variant="send" size="bar" onClick={start}>
                {NEW}
              </Button>
              <AppMenu name={agentName(agent.name)} onPick={onSettings} />
            </>
          }
          pinned
        />
      )}
      {listed.phase === "failed" ? (
        // A read that refused must never be read as an app nobody has spoken to: one draws the
        // composer over a history that is there, the other states why it cannot be shown.
        <PanelEmpty>{listed.message}</PanelEmpty>
      ) : missing ? (
        <PanelEmpty>This conversation is not in {agentName(agent.name)}.</PanelEmpty>
      ) : settling ? null : walled ? (
        <PanelEmpty>This conversation is not shared with this account.</PanelEmpty>
      ) : gated ? (
        // Another member's private conversation is opened by acknowledging it, which is a turn
        // and a record. The address naming it is what reaches the gate, so a link a member was
        // sent lands on the acknowledgement rather than on the transcript.
        <div className="min-h-0 flex-1 overflow-y-auto scrollbar-gutter-stable p-2xl">
          <Disclose
            key={opened.id}
            agent={agent}
            conversation={opened}
            onOpened={() => setDisclosed(opened.id)}
          />
        </div>
      ) : reading ? (
        // A conversation the portal cannot continue is read rather than answered. Where another
        // surface holds it, that surface is the way on and the heading it draws is the way back to
        // it. Where the portal's own surface holds it — a run on a clock, a turn the workspace
        // seeded — there is nowhere to
        // send the member, so the line states the fact and stops: `Reply in Portal` read inside
        // the portal names no act.
        <div className="min-h-0 flex-1 overflow-y-auto scrollbar-gutter-stable p-2xl">
          <ConversationDetail agent={agent} conversation={opened} />
          <p className="mt-2xl max-w-hint text-ink-soft">
            {isPortalChat(opened.surface)
              ? "This conversation is read-only."
              : "This conversation is read-only here. Reply in " +
                surfaceWord(opened.surface) +
                " to continue it."}
          </p>
        </div>
      ) : (
        <Chat
          key={opened?.id ?? railHeld?.conversation_id ?? "new"}
          agent={agent}
          member={member}
          conversationId={opened?.id ?? railHeld?.conversation_id ?? null}
          onCreated={(conversationId, title) => {
            onCreated(conversationId, title);
            setSettles((count) => count + 1);
            onPlace({ ...place, opens: [conversationId] }, "replace");
          }}
          onSettled={() => setSettles((count) => count + 1)}
        />
      )}
    </section>
  );

  /** Every conversation this app holds, newest first, for the member to open one. It is the app's
   *  own index rather than the rail's chats — every surface the app has spoken on, so a thread it
   *  answered in Slack stands here beside the ones started in this lane, and one the member may not
   *  read is named by whose it is. The row states what the conversation is about and where and when
   *  it last moved — the three facts the app's own band states, so one conversation is named the
   *  same wherever it is listed. The day stands at the row's end rather than after its name, since
   *  what the member scans for is the name and every row carries a day; the surface is named only
   *  where it is not this one, the way the rail names none on a conversation the portal holds; and
   *  no mark, because every row here is a conversation and a glyph repeated down the list tells no
   *  two of them apart. The read carries the newest of them and no cursor, so where it says it
   *  stopped at its bound the list says so under the rows rather than ending as though the app had
   *  spoken that many times. Picking a row opens it at its own address, which names a different
   *  lane and puts this list away. */
  const past = (
    <div className="min-h-0 flex-1 overflow-y-auto scrollbar-gutter-stable py-md">
      <Panel
        state={listed}
        shape="table"
        empty={(payload) => ((payload.conversations ?? []).length ? null : NO_HISTORY)}
      >
        {(payload) => (
          <div className="flex flex-col">
            {(payload.conversations ?? []).map((row) => (
              <PressRow
                key={row.id}
                line={subject(row, viewer)}
                note={isPortalChat(row.surface) ? undefined : surfaceWord(row.surface)}
                when={day(row.last_turn_at) ?? undefined}
                onPress={() => onPlace({ ...place, opens: [row.id] }, "push")}
              />
            ))}
            {payload.more ? (
              <p className="m-0 px-lg py-lg text-label text-ink-soft">{HISTORY_BOUND}</p>
            ) : null}
          </div>
        )}
      </Panel>
    </div>
  );

  /** Where the conversation stands when the app has a page: a slot in the screen's own track,
   *  opened by the titlebar's act and named by the conversation it holds. Its band carries the one
   *  act the member cannot reach from the page — starting another conversation with this app —
   *  and no menu to switch between them, because a sidebar click lands any conversation here by
   *  its own address. The act is spent where the pane already stands on a conversation nobody has
   *  spoken in, so it draws as unavailable rather than founding a second empty one. An app with no
   *  page has no track to divide, so the conversation is the screen and takes the width whole. */
  const slot = useSlot(beside && held !== undefined ? (history ? past : conversation) : null, {
    id: lane,
    kind: "panel",
    title: history
      ? HISTORY
      : opened
        ? subject(opened, viewer)
        : (railHeld?.title ?? NEW_CONVERSATION),
    acts: (
      <>
        <Button
          variant="quiet"
          size="icon"
          aria-label={NEW_CONVERSATION + " with " + agentName(agent.name)}
          disabled={held === FRESH}
          onClick={start}
        >
          <IconPlus aria-hidden />
        </Button>
        <Button
          variant="quiet"
          size="icon"
          aria-label={HISTORY + " for " + agentName(agent.name)}
          aria-pressed={history}
          className={cn(history && "bg-fill")}
          onClick={() => setListing(history ? null : lane)}
        >
          <IconHistory aria-hidden />
        </Button>
      </>
    ),
    // The way out shuts what is standing: the list first, then the lane under it. A single X that
    // took the lane away from a member reading the list would shut two things on one press.
    onClose: history
      ? () => setListing(null)
      : () => onPlace({ ...place, opens: [] }, "replace"),
  });

  if (beside) {
    return (
      <>
        <section
          aria-label={agentName(agent.name) + " homepage"}
          className="relative flex min-h-0 min-w-0 flex-1 flex-col"
        >
          {/* The page heads itself — it is the portal's own screen and draws the band a section
              drew, so a pane band over it would state the name twice and push the page down a row
              it never had. The two acts that are the shell's own stand on the band line at its
              right end, and the page's band makes room for them: the frame inherits the inset the
              shell's acts occupy, so its own band-right controls end where these begin. */}
          <div className="absolute top-lg right-2xl z-10 flex items-center gap-xs">
            <AppMenu
              name={agentName(agent.name)}
              onPick={onSettings}
              className="rounded-full border border-edge bg-surface"
            />
            <Button
              variant="quiet"
              size={held !== undefined ? "icon" : "bar"}
              aria-label={
                (held !== undefined ? "Close chat with " : "Chat with ") + agentName(agent.name)
              }
              aria-pressed={held !== undefined}
              className={cn(
                "rounded-full border border-edge bg-surface",
                held !== undefined && "bg-fill",
              )}
              onClick={() =>
                held === undefined
                  ? onPlace(
                      { ...place, opens: [opened?.id ?? editing?.conversation_id ?? FRESH] },
                      "push",
                    )
                  : onPlace({ ...place, opens: [] }, "replace")
              }
            >
              {held !== undefined ? (
                <IconLayoutSidebarRight aria-hidden />
              ) : (
                <>
                  <IconMessage className="size-(--size-glyph)" aria-hidden />
                  Chat
                </>
              )}
            </Button>
          </div>
          {/* Each frame's key carries the deploy generation, so a redeploy at the same URL mounts
              a fresh copy rather than showing the page the member last loaded — arriving invisible
              over the standing one and fading in on its own load, so the swap never paints the
              blank document. The box and the frames wear the pane's own background and the portal's
              color-scheme, so what shows through an empty frame is the pane rather than a browser's
              white canvas. */}
          <div className="relative min-h-0 flex-1 bg-surface">
            {frames.map((frame, index) => (
              <iframe
                key={frame.key}
                ref={index === frames.length - 1 ? frameRef : undefined}
                src={frame.url}
                title={agentName(agent.name) + " homepage"}
                sandbox={HOMEPAGE_SANDBOX}
                referrerPolicy="no-referrer"
                allow="fullscreen"
                onLoad={() => landed(frame.key)}
                className={cn(
                  "absolute inset-0 size-full border-0 bg-surface",
                  "transition-opacity duration-200 ease-out [color-scheme:inherit]",
                  frame.loaded ? "opacity-100" : "pointer-events-none opacity-0",
                )}
              />
            ))}
          </div>
        </section>
        {slot}
      </>
    );
  }

  if (railRow && railAgent && target !== undefined) {
    return (
      <section
        aria-label={railRow.title}
        className="flex min-h-0 min-w-0 flex-1 flex-col"
      >
        <Header heading={2} title={railRow.title} pinned />
        <Chat
          key={target}
          agent={railAgent}
          member={member}
          conversationId={target}
          onCreated={onCreated}
          onSettled={() => setSettles((count) => count + 1)}
        />
      </section>
    );
  }
  return (
    <>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">{conversation}</div>
    </>
  );
}
