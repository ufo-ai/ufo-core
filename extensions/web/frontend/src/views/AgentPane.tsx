import { useState } from "react";
import { IconArrowUpRight, IconMessage, IconSettings } from "@tabler/icons-react";

import { Button, buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Header } from "@/kernel/pane";
import { PanelEmpty, usePanelRead } from "@/kernel/panel";
import { useSlot } from "@/kernel/slots";
import { isPortalChat, surfaceWord, useViewer } from "@/lib/audience";
import { agentName } from "@/lib/agentName";
import { cn } from "@/lib/cn";
import type { ChatRow } from "@/lib/rail";
import { Chat } from "@/views/Chat";
import { ConversationDetail, Disclose, subject } from "@/views/Conversations";
import type { PlaceStep, WorkspacePlace } from "@/lib/route";
import type { Agent, Conversation, Member } from "@/lib/types";

/** The homepage the app's binding names, the answer that one is being built, or the answer that
 *  it has none. */
type HomepageRead = { state: "set"; url: string } | { state: "building" } | { state: "none" };

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

/** The lane the app's conversations are read down, and the name it states. The lane is not named in
 *  the address: `opens` says which conversation the pane holds, and a list standing there too would
 *  take the one place that answers that. */
const INDEX = "conversations";
const CONVERSATIONS = "Conversations";


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
 *  Wherever a conversation stands, the app's own conversations stand in a lane beside it, which is
 *  how a member holding one of several reaches the rest without leaving the app. */
export function AgentPane({
  agent,
  member,
  chats,
  place,
  onSettings,
  onCreated,
  onPlace,
}: AgentPaneProps) {
  const [settles, setSettles] = useState(0);
  const viewer = useViewer();
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
  const live =
    chats === null
      ? null
      : new Set(
          chats
            .filter((row) => row.agent_id === agent.id && row.mine && isPortalChat(row.surface))
            .map((row) => row.conversation_id),
        );
  // Which conversation the half is holding — the one slot this screen's track carries. An address
  // naming one is the member's pick and is answered by that conversation or by nothing, never by
  // another one, because a link a member was sent opening a different conversation than it names is
  // worse than a link that says it cannot be opened. An address naming none opens the newest the
  // member can speak in, so arriving lands on the work rather than on a list of it, and on an app
  // they have only ever read, on the composer. Nothing is written to the address for that landing:
  // a redirect on arrival costs a history entry the member did not ask for, and the app's own
  // address already means "the latest".
  const held = place.opens?.[0];
  const wanted = held === FRESH;
  const named =
    held !== undefined && !wanted ? (rows.find((entry) => entry.id === held) ?? null) : null;
  const missing = held !== undefined && !wanted && named === null && listed.phase === "ready";
  // Acknowledging is recorded, not reflected: the index answers `readable` from audience membership
  // alone, so a conversation this member has just opened still arrives false and would be handed
  // back its own gate — and every press would write another audit row for a disclosure already
  // made. What the member did in this pane is held here, the way a permalink's pane holds it.
  const [disclosed, setDisclosed] = useState<string | null>(null);
  const start = () => onPlace({ ...place, opens: [FRESH] }, "push");
  const opened = wanted
    ? null
    : (named ?? (live === null ? null : (rows.find((entry) => live.has(entry.id)) ?? null)));
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
  // The half stands for a homepage that exists and for one being made; an app that has neither
  // draws one column, because a column whose only content is the sentence that it is empty takes
  // half the screen to say what the app having no homepage already says.
  const beside = url !== null || building;

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

  // A conversation the member can neither read nor acknowledge is not one this list opens: the pane
  // answers it with the sentence that it is not shared with this account, which is not a place a
  // press can land the member.
  const listable = rows.filter((entry) => entry.readable || entry.disclosable);

  /** The app's conversations, standing where the one being read can be seen beside them. A press
   *  writes the address and nothing else, so the address stays the one answer to which conversation
   *  the pane holds and the row marked current is read back off it. The lane states its own name and
   *  carries no act: at a lane's floor the band has room for the name, the mark and the way out, and
   *  the acts on this screen act on the app rather than on the list.
   *
   *  It stands wherever a conversation does — beside the page once the member has opened the chat
   *  over it, and beside the conversation that is the whole screen where the app has no page. A page
   *  standing alone has no conversation to be read beside, and an app nobody has spoken to has
   *  nothing to list. */
  const index = useSlot(
    listable.length > 0 && (!beside || held !== undefined) ? (
      <ul className="m-0 flex min-h-0 flex-1 list-none flex-col gap-px overflow-y-auto p-sm">
        {listable.map((entry) => (
          <li key={entry.id}>
            <button
              type="button"
              aria-current={entry.id === opened?.id}
              onClick={() => onPlace({ ...place, opens: [entry.id] }, "push")}
              className={cn(
                "flex h-(--size-row) w-full items-center rounded-row border-0 bg-transparent",
                "px-sm text-left text-label text-inherit hover:bg-fill",
                entry.id === opened?.id && "bg-fill",
              )}
            >
              <span className="min-w-0 truncate">{subject(entry, viewer)}</span>
            </button>
          </li>
        ))}
      </ul>
    ) : null,
    { id: INDEX, kind: "index", title: CONVERSATIONS },
  );

  /** Where the conversation stands when the app has a page: a slot in the screen's own track,
   *  opened by the titlebar's act and named by the conversation it holds. An app with no page has
   *  no track to divide, so the conversation is the screen and takes the width whole. */
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
          className="flex min-h-0 min-w-0 flex-1 flex-col"
        >
          <Header
            heading={2}
            title={agentName(agent.name)}
            acts={
              <>
                {/* A link, not a button: this is the one act on the screen that leaves the portal,
                    so it keeps the shape a member already knows how to open in a tab of their own.
                    It wears the icon control's box so it stands level with the acts beside it
                    across the band's one line. A homepage still being built has no address. */}
                {url !== null ? (
                  <a
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    aria-label={"Open " + agentName(agent.name) + " homepage"}
                    className={cn(buttonVariants({ variant: "quiet", size: "icon" }))}
                  >
                    <IconArrowUpRight aria-hidden />
                  </a>
                ) : null}
                <Button
                  variant="quiet"
                  size="icon"
                  aria-label={"Settings for " + agentName(agent.name)}
                  onClick={onSettings}
                >
                  <IconSettings aria-hidden />
                </Button>
                <Button
                  variant="quiet"
                  size="icon"
                  aria-label={"Chat with " + agentName(agent.name)}
                  aria-pressed={held !== undefined}
                  className={cn(held !== undefined && "bg-fill")}
                  onClick={() =>
                    held === undefined
                      ? onPlace({ ...place, opens: [opened?.id ?? FRESH] }, "push")
                      : onPlace({ ...place, opens: [] }, "replace")
                  }
                >
                  <IconMessage aria-hidden />
                </Button>
              </>
            }
            pinned
          />
          {url === null ? (
            <Building />
          ) : (
            /* The frame is the app's own trusted page and carries the sandbox around the
               model-authored bytes itself, so this iframe takes no sandbox attribute — sandbox
               flags inherit, and the inner site is promised scripts. */
            <iframe
              src={url}
              title={agentName(agent.name) + " homepage"}
              className="min-h-0 flex-1 border-0"
            />
          )}
        </section>
        {index}
        {slot}
      </>
    );
  }

  return (
    <>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">{conversation}</div>
      {index}
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

