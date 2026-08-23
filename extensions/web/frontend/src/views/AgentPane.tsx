import { useEffect, useRef, useState } from "react";
import { IconMessage, IconSettings } from "@tabler/icons-react";

import { attachBridge, type BridgeHandle } from "@/lib/bridge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Header } from "@/kernel/pane";
import { PanelEmpty, usePanelRead } from "@/kernel/panel";
import { useSlot } from "@/kernel/slots";
import { isPortalChat, surfaceWord, useViewer } from "@/lib/audience";
import { agentName } from "@/lib/agentName";
import { CHAT_SURFACE, useAgents } from "@/lib/mainAgent";
import { cn } from "@/lib/cn";
import type { ChatRow } from "@/lib/rail";
import { Chat } from "@/views/Chat";
import { ConversationDetail, Disclose, subject } from "@/views/Conversations";
import { mergePlace, serializePlace, type PlaceStep, type WorkspacePlace } from "@/lib/route";
import type { Agent, Conversation, Member } from "@/lib/types";

/** The homepage the app's binding names, the answer that one is being built, or the answer that
 *  it has none. `deploy_generation` bumps on every redeploy of the bound page, so the frame
 *  remounts onto fresh bytes at the same URL (RFC 0039 Contract 2); an older payload without it
 *  reads as 0. */
type HomepageRead =
  | { state: "set"; url: string; deploy_generation?: number }
  | { state: "building" }
  | { state: "none" };

/** How often the half asks again while a homepage is being built. A build is minutes of work the
 *  member is watching for the end of, so the read runs faster than the pane's resting rate — and
 *  only while it is running, because the answer cannot change on its own once it has settled. */
const BUILDING_POLL_MS = 5_000;

/** What the half is called before a conversation exists to name it, and the act that starts one.
 *  The act stands with the acts at the far end of the band, where every act on the whole surface
 *  stands. */
const NEW_CONVERSATION = "New conversation";
const NEW = "New";

/** The slot a conversation nobody has founded yet stands in. Every other slot on this screen is
 *  named by the conversation it holds; this one has no conversation to name it, and the send that
 *  founds one writes that conversation's id over it. */
const FRESH = "new";

/** How long the arriving frame takes to fade over the one it replaces, and how long after that the
 *  replaced frame is kept mounted under it. The page it swaps in has already fired `load`, so the
 *  fade is the whole wait — nothing here delays the swap past the paint it exists to smooth. */
const FRAME_SWAP_MS = 200;

/** One mounted copy of the homepage frame. A redeploy remounts the page at the same URL, and a
 *  frame torn down the instant its successor mounts leaves the member watching the successor's
 *  blank document paint — so the standing copy holds the screen until the arriving one has
 *  loaded, and the two cross-fade. `key` is the url and deploy generation together, the identity
 *  the pane already remounts on. */
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
  onSettings: () => void;
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
  const viewer = useViewer();
  const agents = useAgents();
  // Two reads, each authoritative for a different question. The index says which conversations the
  // app has at all — every surface it has ever spoken on. The rail says which of them the portal
  // can carry on, because the chat transport answers only for a conversation the web surface
  // founded. Neither answers the other's question: an index row on Slack has no composer, and a
  // rail filtered to this app is not the app's history.
  const listed = usePanelRead<{ conversations: Conversation[] }>(
    "/agents/" + agent.id + "/conversations",
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
  const target = place.opens?.[0];
  const conversational =
    target !== undefined && (target === FRESH || rows.some((entry) => entry.id === target));
  const held = conversational ? target : undefined;
  const wanted = held === FRESH;
  const named =
    held !== undefined && !wanted ? (rows.find((entry) => entry.id === held) ?? null) : null;
  // Acknowledging is recorded, not reflected: the index answers `readable` from audience membership
  // alone, so a conversation this member has just opened still arrives false and would be handed
  // back its own gate — and every press would write another audit row for a disclosure already
  // made. What the member did in this pane is held here, the way a permalink's pane holds it.
  const [disclosed, setDisclosed] = useState<string | null>(null);
  const start = () => onPlace({ ...place, opens: [FRESH] }, "push");
  const opened = wanted
    ? null
    : (named ??
      (live === null
        ? null
        : (rows.find((entry) => entry.id === editing?.conversation_id) ??
          rows.find((entry) => live.has(entry.id)) ??
          null)));
  // The read polls at its own rate while a build is running, so the page arrives on its own rather
  // than on the member's next reload; the rate is read off the last answer, so it drops back to the
  // pane's resting one the moment the build settles.
  const [building, setBuilding] = useState(false);
  // What the half is drawing, named rather than spelled inline: four states read as a chain of
  // conditions no one can follow. Nothing is drawn while the reads that decide are still in
  // flight — a composer put up for that frame is one the member could type into, and the words
  // would be founded on a conversation the next answer replaces.
  const settling = opened === null && !wanted && (live === null || listed.phase === "loading");
  // A conversation shared with nobody this member belongs to is not one an acknowledgement can
  // open: the index says so on the row itself, and the intent would refuse. The half says that
  // rather than offering an act that cannot be taken.
  const walled = opened !== null && !opened.readable && !opened.disclosable;
  const gated = opened !== null && !walled && !opened.readable && disclosed !== opened.id;
  const reading = opened !== null && !walled && !gated && live !== null && !live.has(opened.id);

  const site = usePanelRead<HomepageRead>(
    "/agents/" + agent.id + "/homepage",
    settles,
    building ? BUILDING_POLL_MS : undefined,
  );
  // A read that failed says so and stops claiming a build is running: a half stuck on the skeleton
  // polls no more (the read gives up after a failure) and would never come back on its own.
  const state = site.phase === "ready" ? site.payload.state : site.phase === "failed" ? "none" : null;
  if (state !== null && building !== (state === "building")) setBuilding(state === "building");
  const url = site.phase === "ready" && site.payload.state === "set" ? site.payload.url : null;
  const generation =
    site.phase === "ready" && site.payload.state === "set"
      ? (site.payload.deploy_generation ?? 0)
      : 0;
  // The half stands for a homepage that exists and for one being made; an app that has neither
  // draws one column, because a column whose only content is the sentence that it is empty takes
  // half the screen to say what the app having no homepage already says.
  const beside = url !== null || building;
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
    state !== null &&
    listed.phase === "ready";
  // The bridge is how the framed page reads the member's data and drives navigation; it is bound to
  // the live frame and rebound when a redeploy remounts it under a new key, so each set of bytes
  // talks to exactly one listener. The page stands at this pane's own place, less the track the pane
  // holds as a slot itself — one meaning per channel, so nothing is opened twice. The place rides
  // `init` on a fresh frame and the bridge's own `place` message while the frame stands, because a
  // rail click lands on a page that already booted. It crosses whole: a frame handed one key of a
  // place could only stand the screen the address names by guessing the rest.
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
    const frameKey = url + ":" + generation;
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
      agentId: agent.id,
      place: framedRef.current,
      chatSurface: agent.app === CHAT_SURFACE,
      onCreated: (agentId, conversationId, title) =>
        foundedRef.current(agentId, conversationId, title),
    });
    bridgeRef.current = handle;
    return () => {
      bridgeRef.current = null;
      handle.detach();
    };
  }, [member, agent.id, agent.app, url, generation]);
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
          title={opened ? subject(opened, viewer) : NEW_CONVERSATION}
          acts={
            <>
              <Button variant="send" size="bar" onClick={start}>
                {NEW}
              </Button>
              <Button
                variant="quiet"
                size="icon"
                aria-label={"Settings for " + agentName(agent.name)}
                onClick={onSettings}
              >
                <IconSettings aria-hidden />
              </Button>
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
        // A conversation the portal did not found is read rather than answered: the composer
        // would post to a route that refuses it. Where another surface holds it, that surface is
        // the way on and the heading it draws is the way back to it. Where the portal's own
        // surface holds it — a run on a clock, a turn the workspace seeded — there is nowhere to
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
          key={opened?.id ?? "new"}
          agent={agent}
          member={member}
          conversationId={opened?.id ?? null}
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

  /** Where the conversation stands when the app has a page: a slot in the screen's own track,
   *  opened by the titlebar's act and named by the conversation it holds. The app has one editing
   *  conversation, so the slot's band carries no act to start another and no menu to switch — the
   *  toggle opens the one there is, and a sidebar click still lands any directive conversation
   *  here by its own address. An app with no page has no track to divide, so the conversation is
   *  the screen and takes the width whole. */
  const slot = useSlot(beside && held !== undefined ? conversation : null, {
    id: held ?? FRESH,
    kind: "panel",
    title: opened ? subject(opened, viewer) : NEW_CONVERSATION,
    onClose: () => onPlace({ ...place, opens: [] }, "replace"),
  });

  if (beside) {
    return (
      <>
        <section
          aria-label={agentName(agent.name) + " homepage"}
          aria-busy={url === null}
          className="relative flex min-h-0 min-w-0 flex-1 flex-col"
        >
          {/* The page heads itself — it is the portal's own screen and draws the band a section
              drew, so a pane band over it would state the name twice and push the page down a row
              it never had. The two acts that are the shell's own stand on the band line at its
              right end, and the page's band makes room for them: the frame inherits the inset the
              shell's acts occupy, so its own band-right controls end where these begin. */}
          <div className="absolute top-lg right-2xl z-10 flex items-center gap-xs">
            <Button
              variant="quiet"
              size="icon"
              aria-label={"Settings for " + agentName(agent.name)}
              className="rounded-full border border-edge bg-surface"
              onClick={onSettings}
            >
              <IconSettings aria-hidden />
            </Button>
            <Button
              variant="quiet"
              size="icon"
              aria-label={"Chat with " + agentName(agent.name)}
              aria-pressed={held !== undefined}
              className={cn(
                "rounded-full border border-edge bg-surface",
                held !== undefined && "bg-fill",
              )}
              onClick={() =>
                held === undefined
                  ? onPlace({ ...place, opens: [opened?.id ?? FRESH] }, "push")
                  : onPlace({ ...place, opens: [] }, "replace")
              }
            >
              <IconMessage aria-hidden />
            </Button>
          </div>
          {url === null ? (
            <Building />
          ) : (
            /* The frame is the app's own trusted page and carries the sandbox around the
               model-authored bytes itself, so these iframes take no sandbox attribute — sandbox
               flags inherit, and the inner site is promised scripts. Each frame's key carries the
               deploy generation, so a redeploy at the same URL mounts a fresh copy rather than
               showing the page the member last loaded — arriving invisible over the standing one
               and fading in on its own load, so the swap never paints the blank document. The box
               and the frames wear the pane's own background and the portal's color-scheme, so what
               shows through an empty frame is the pane rather than a browser's white canvas. */
            <div className="relative min-h-0 flex-1 bg-surface">
              {frames.map((frame, index) => (
                <iframe
                  key={frame.key}
                  ref={index === frames.length - 1 ? frameRef : undefined}
                  src={frame.url}
                  title={agentName(agent.name) + " homepage"}
                  onLoad={() => landed(frame.key)}
                  className={cn(
                    "absolute inset-0 size-full border-0 bg-surface",
                    "transition-opacity duration-200 ease-out [color-scheme:light_dark]",
                    frame.loaded ? "opacity-100" : "pointer-events-none opacity-0",
                  )}
                />
              ))}
            </div>
          )}
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

/** The page while it is being written: the shape a page takes — a heading, a rule under it, a few
 *  lines of body — rather than a spinner, so the half holds the frame's own rhythm and the built
 *  page lands into a column the eye is already reading. The bars are three because the count says
 *  nothing about the page coming; what they say is that this half is a page and not a control. */
function Building() {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-lg overflow-hidden p-2xl">
      <Skeleton className="h-(--size-notice) w-2/5" />
      <div className="border-b border-edge" />
      <Skeleton className="h-(--size-notice) w-full" />
      <Skeleton className="h-(--size-notice) w-full" />
      <Skeleton className="h-(--size-notice) w-full" />
    </div>
  );
}

