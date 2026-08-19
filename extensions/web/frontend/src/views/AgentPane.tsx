import { useState } from "react";
import { IconArrowUpRight, IconChevronDown, IconSettings } from "@tabler/icons-react";

import { Button, buttonVariants } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { PanelEmpty, usePanelRead } from "@/kernel/panel";
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

/** The band a half is headed by. Both halves wear it, so the two titles stand on one line across
 *  the pane and one rule runs under them. */
const HEADER =
  "flex h-(--size-control) shrink-0 items-center gap-2xl border-b border-edge px-2xl py-lg box-content";

/** What the half is called before a conversation exists to name it, and the act that starts one.
 *  The act stands beside settings rather than inside the switcher: it makes a conversation instead
 *  of choosing among the ones there are, and a list of what exists is the wrong place to put the
 *  thing that does not exist yet. */
const NEW_CONVERSATION = "New conversation";
const NEW = "New";

/** What the second half is called. The site's own address is not a title — a member reading an app
 *  already knows which one they opened, and the address is what the act beside it opens. */
const HOME = "Home";

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
   *  holding — the switcher reads the rail, so a chat the rail has not heard of is one the half
   *  would slide off the moment anything re-read. */
  onCreated: (conversationId: string, title: string) => void;
  onPlace: (place: WorkspacePlace, step: PlaceStep) => void;
};

/** The wide pane of the apps screen, in two halves: talking to the app on the left, what it built
 *  on the right. They are read at once rather than switched between, because they are one fact — a
 *  member asking for a page change is looking at that page — and the halves are equal because
 *  neither is the other's aside. Each half heads itself, so the rule between them is the whole of
 *  the division and no band spans both to say it again.
 *
 *  Arriving opens the conversation that moved last, so the screen lands on the work rather than on
 *  a list of it; an app nobody has spoken to opens the composer instead, because the first thing a
 *  member does with a new app is talk to it.
 *
 *  An app that has built no site draws one half. A column whose only content is the sentence that
 *  it is empty takes half the screen to say what a member learns from the app having no site at
 *  all.
 *
 *  A screen too narrow for two columns reads them as two rows, the site under the conversation at
 *  the height a page fingerprint is read at. Hiding it there would leave the member no way to the
 *  thing the app built, since the way out to it stands in the half that would be gone. */
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
  // Which conversation the half is holding. An address naming one is the member's pick and is
  // answered by that conversation or by nothing — never by another one, because a link a member
  // was sent opening a different conversation than it names is worse than a link that says it
  // cannot be opened. An address naming none opens the newest the member can speak in, so arriving
  // lands on the work rather than on a list of it, and on an app they have only ever read, on the
  // composer. Nothing is written to the address for that landing: a redirect on arrival costs a
  // history entry the member did not ask for, and the app's own address already means "the latest".
  const named = place.open ? (rows.find((entry) => entry.id === place.open) ?? null) : null;
  const missing = place.open !== undefined && named === null && listed.phase === "ready";
  // Starting a fresh conversation is the member's last press rather than a place, because a
  // conversation that does not exist yet has nothing to name — so it is dropped the moment the
  // address names one, and the send that founds it puts its id there. The press has to clear the
  // address as well as set this: an address still naming a conversation would go on answering for
  // the half, and the act would do nothing on every screen but the one where nothing is open.
  const [starting, setStarting] = useState(false);
  // Acknowledging is recorded, not reflected: the index answers `readable` from audience membership
  // alone, so a conversation this member has just opened still arrives false and would be handed
  // back its own gate — and every press would write another audit row for a disclosure already
  // made. What the member did in this pane is held here, the way a permalink's pane holds it.
  const [disclosed, setDisclosed] = useState<string | null>(null);
  const wanted = starting && place.open === undefined;
  const start = () => {
    setStarting(true);
    onPlace({ ...place, open: undefined }, "push");
  };
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
  const gated = opened !== null && !opened.readable && disclosed !== opened.id;
  const reading = opened !== null && !gated && live !== null && !live.has(opened.id);

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

  return (
    <div
      className={cn(
        "grid min-h-0 min-w-0 grid-cols-1",
        beside && "grid-cols-2 max-narrow:grid-cols-1 max-narrow:grid-rows-[minmax(0,1fr)_auto]",
      )}
    >
      <section
        aria-label={agentName(agent.name)}
        className={cn(
          "flex min-h-0 min-w-0 flex-col",
          beside && "border-r border-edge max-narrow:border-r-0",
        )}
      >
        <header className={HEADER}>
          <Switcher
            title={opened ? subject(opened, viewer) : NEW_CONVERSATION}
            held={opened?.id ?? null}
            rows={rows}
            viewer={viewer}
            onOpen={(id) => {
              setStarting(false);
              onPlace({ ...place, open: id }, "push");
            }}
          />
          <Button
            variant="send"
            size="bar"
            className="ml-auto shrink-0"
            onClick={start}
          >
            {NEW}
          </Button>
          <Button
            size="icon"
            aria-label={"Settings for " + agentName(agent.name)}
            className="shrink-0 rounded-full"
            onClick={onSettings}
          >
            <IconSettings className="size-icon" aria-hidden />
          </Button>
        </header>
        {listed.phase === "failed" ? (
          // A read that refused must never be read as an app nobody has spoken to: one draws the
          // composer over a history that is there, the other states why it cannot be shown.
          <PanelEmpty>{listed.message}</PanelEmpty>
        ) : missing ? (
          <PanelEmpty>This conversation is not in {agentName(agent.name)}.</PanelEmpty>
        ) : settling ? null : gated ? (
          // Another member's private conversation is opened by acknowledging it, which is a turn
          // and a record. The gate is reachable from here because this is where the app's
          // conversations are listed; nothing else on this screen leads to it.
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
              setStarting(false);
              onCreated(conversationId, title);
              setSettles((count) => count + 1);
              onPlace({ ...place, open: conversationId }, "replace");
            }}
            onSettled={() => setSettles((count) => count + 1)}
          />
        )}
      </section>
      {beside ? (
        <section
          aria-label={agentName(agent.name) + " homepage"}
          aria-busy={url === null}
          className={cn(
            "flex min-h-0 min-w-0 flex-col",
            "max-narrow:h-(--media-tall) max-narrow:border-t max-narrow:border-edge",
          )}
        >
          <header className={HEADER}>
            <h2 className="m-0 min-w-0 flex-1 truncate text-subtitle font-medium">{HOME}</h2>
            {/* A link, not a button: this is the one act on the screen that leaves the portal, so
                it keeps the shape a member already knows how to open in a tab of their own. It
                wears the icon control's box so it stands level with the settings act across the
                pane's one header line. A homepage still being built has no address to open. */}
            {url !== null ? (
              <a
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                aria-label={"Open " + agentName(agent.name) + " homepage"}
                className={cn(buttonVariants({ size: "icon" }), "shrink-0 rounded-full")}
              >
                <IconArrowUpRight className="size-icon" aria-hidden />
              </a>
            ) : null}
          </header>
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
      ) : null}
    </div>
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

/** What the half is holding, and the way to another. The title is the control because the title is
 *  what changes — a member switching conversation presses the name of the one they are reading, not
 *  a word beside it. It lists the page of conversations the index answers with rather than a recent
 *  few, and scrolls, the way any list too long for its container does; past that page the chat rail
 *  is where a conversation is found by name. */
function Switcher({
  title,
  held,
  rows,
  viewer,
  onOpen,
}: {
  title: string;
  /** The one shown, ticked in the list — null while a fresh conversation is being started, which
   *  the list's own first item already names. */
  held: string | null;
  rows: Conversation[];
  viewer: string | null;
  onOpen: (id: string) => void;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className={cn(
            "flex min-w-0 flex-1 items-center gap-xs rounded-control border-0 bg-transparent",
            "p-0 text-start text-subtitle font-medium text-inherit",
          )}
        >
          <span className="min-w-0 truncate">{title}</span>
          <IconChevronDown className="size-(--size-glyph) shrink-0 text-ink-soft" aria-hidden />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className="max-h-(--media-tall) w-(--container-threads) overflow-y-auto"
      >
        <DropdownMenuRadioGroup value={held ?? ""}>
          {rows
            .filter((entry) => entry.readable || entry.disclosable)
            .map((entry) => (
              <DropdownMenuRadioItem
                key={entry.id}
                value={entry.id}
                onSelect={() => onOpen(entry.id)}
              >
                <span className="min-w-0 truncate">{subject(entry, viewer)}</span>
              </DropdownMenuRadioItem>
            ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
