import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import {
  IconChevronDown,
  IconCirclePlus,
  IconDeviceDesktop,
  IconLogout,
  IconMenu2,
  IconMoon,
  IconPlug,
  IconPlus,
  IconSettings,
  IconSun,
  IconUsers,
  IconX,
} from "@tabler/icons-react";

import logo from "@/assets/ufo-logo.svg";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { SILENT, Toast } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Admin } from "@/views/Admin";
import { AgentSetup } from "@/views/AgentSetup";

import { AgentBuilder, Agents, AppsIndex } from "@/views/Agents";
import { AppsProvider } from "@/views/Apps";
import { Chat } from "@/views/Chat";
import { ChatPane, ConversationSlot } from "@/views/ChatPane";
import { ConversationSlotPane } from "@/views/ConversationSlotPane";
import { ConversationDetail, Disclose, subject } from "@/views/Conversations";
import { MinimalSidebar } from "@/components/MinimalSidebar";
import { FirstRun } from "@/views/FirstRun";
import { Home } from "@/views/Home";
import { SignIn } from "@/views/SignIn";
import { Shortcuts } from "@/views/Shortcuts";
import { Spotlight } from "@/views/Spotlight";
import { TabbedPane } from "@/views/TabbedPane";
import { CONNECTORS, SECTION_VIEWS, WORKSPACE_VIEWS, type PaneView } from "@/views/registry";
import { WEB_SURFACE, isPortalChat, Viewer, WorkspaceId, surfaceWord, useViewer } from "@/lib/audience";
import { SIGN_OUT_PATH } from "@/lib/api";
import { useAppStatus } from "@/lib/appStatusStore";
import { DrawerHost, useDrawerList, useDrawerSlot } from "@/kernel/drawer";
import { COLUMN, Header, Pane, PaneNote } from "@/kernel/pane";
import { Waiting } from "@/kernel/panel";
import { CHAT_SURFACE, MainAgentProvider, chatSurface } from "@/lib/mainAgent";
import { cn } from "@/lib/cn";
import { SCHEME_OPTIONS, pickScheme, useScheme, type Scheme } from "@/lib/scheme";
import { SETUP, pageCrumb, pageTitle, type Crumb } from "@/lib/title";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { stampIso } from "@/lib/rail";
import { SurfaceMark } from "@/lib/surfaceMark";
import {
  pickPinned,
  pickSectionShut,
  quietRail,
  railActivity,
  railFounded,
  readRail,
  seekChat,
  useRail,
} from "@/lib/railStore";
import {
  forwardApps,
  heldRoute,
  openAdmin,
  openAgent,
  openAgentPlace,
  openApps,
  openBuilder,
  openChat,
  openHome,
  openHomeChat,
  openNewChat,
  openSlot,
  placeAgent,
  placeHome,
  placeSection,
  placeWorkspace,
  startRouter,
  useRoute,
} from "@/lib/router";
import type { Seek } from "@/kernel/slots";
import {
  COMPOSE,
  COMPOSING,
  HOME_NEW_LANE,
  WORKSPACE_TABS,
  homeLaneAgent,
  homeLaneConversation,
  mintHomeLane,
  standing,
  type Route,
  type Section,
  type WorkspacePlace,
  type WorkspaceTab,
} from "@/lib/route";
import { clearChat } from "@/lib/chatStore";
import { setPendingAsk } from "@/lib/pendingAsk";
import { TRACK_MAX_SLOTS, heldTrack } from "@/lib/tracks";
import { ALL_SURFACES, SurfacesProvider, useSurfaces } from "@/lib/surfaces";
import type { Agent, ArchivedApp, Member, OwnedConversation, Surfaces } from "@/lib/types";
import { useNarrow } from "@/lib/narrow";

export type AppProps = {
  agents: Agent[];
  archived?: ArchivedApp[];
  member: Member;
  surfaces?: Surfaces;
  onAgents: () => void;
};

/** Whether the member stands in an app the workspace is still building: the app's setup address or
 *  its own address — the pane replaces the second with the first — while the boot read says the app
 *  stands on that setup screen.
 *
 *  An app that has been built reaches the same setup address of its own accord, to reconnect an
 *  account, and that is not this: the member is somewhere they move around from, and the answer is
 *  no. An app the roster does not carry is not one either — the pane answers `No such app.` */
function inSetup(route: Route, agents: Agent[]): boolean {
  if (route.kind !== "agent" && route.kind !== "agent-setup") return false;
  return agents.some((agent) => agent.id === route.agentId && agent.stands_on_setup === true);
}

/** The track a press on the rail's + leaves home standing. The picker is spelled one way, so it
 *  stands at most once and a press while it stands is a press on the tab that is already there.
 *  A new tab enters at the left end, beside the rail the press came from; under the cap the row
 *  slides right to make room, and at the cap the right-hand lane falls off — the row ages
 *  rightward, so the lane a member has read longest is the one that goes. */
function newTab(opens: string[]): string[] {
  if (opens.includes(HOME_NEW_LANE)) return opens;
  if (opens.length < TRACK_MAX_SLOTS) return [HOME_NEW_LANE, ...opens];
  return [HOME_NEW_LANE, ...opens.slice(0, -1)];
}

/** Apps the workspace gained after this page loaded. A workspace ships its apps on its first turn,
 *  which is a turn the member takes from inside an already-loaded portal — so the boot read that
 *  seeded the sidebar predates every one of them, and without this the column states one app until
 *  the member happens to reload.
 *
 *  The status read is the signal, and it costs nothing: it already answers for every agent the
 *  member reaches, so an id the boot read never carried is the workspace having gained one. Each id
 *  is asked about once. A re-read that comes back without it — an app this member may not read —
 *  must not send the next tick asking again. */
function useProvisioned(agents: Agent[], onAgents: () => void): void {
  const { statuses } = useAppStatus();
  const asked = useRef<Set<string>>(new Set());
  useEffect(() => {
    const known = new Set(agents.map((agent) => agent.id));
    const gained = Object.keys(statuses).filter((id) => !known.has(id) && !asked.current.has(id));
    if (!gained.length) return;
    for (const id of gained) asked.current.add(id);
    onAgents();
  }, [statuses, agents, onAgents]);
}

export function App({
  agents,
  archived = [],
  member,
  surfaces = ALL_SURFACES,
  onAgents,
}: AppProps) {
  const route = useRoute();
  const rail = useRail();
  const [menu, setMenu] = useState(false);
  const shutMenu = useCallback(() => setMenu(false), []);
  const narrow = useNarrow();
  useScrollMark();
  const mainAgent = agents.find((agent) => agent.main) ?? agents[0] ?? null;
  /** What the navigation draws. An app the deploy withholds is still the workspace's, still opens
   *  from its address, and still answers a picker — it is kept out of the sidebar, the flyout and
   *  the apps listing, which is the whole of what hiding one means. `mainAgent` reads the whole set
   *  above, so withholding the assistant costs the composer and the first run nothing. */
  const listed = agents.filter((agent) => !agent.hidden);

  /** The lanes home stands, for the rail that lists them. The address answers while the member is
   *  standing on home and the row home was left holding answers everywhere else — the two the
   *  router keeps in step, so the rail names the lanes the member will find when they go back.
   *
   *  A tile is the app the lane stands, which a conversation's lane names through the rail row it
   *  was picked off — the rail is where that lane was found and where it is resolved. A lane
   *  resolving to no app this roster holds is not listed: the rail is a way to a lane, and a row it
   *  cannot name is a row nobody can read. */
  const homePlace: WorkspacePlace = route.kind === "home" ? route.place : {};
  const homeOpens = useMemo(
    () => homePlace.opens ?? heldTrack("home"),
    [homePlace.opens, route],
  );
  const homeLanes = useMemo(
    () =>
      homeOpens
        .map((lane) => {
          const conversationId = homeLaneConversation(lane);
          const agentId =
            conversationId === null
              ? homeLaneAgent(lane)
              : (rail.rows.find((row) => row.conversation_id === conversationId)?.agent_id ??
                null);
          const agent = agents.find((entry) => entry.id === agentId);
          return agent ? { lane, agent } : null;
        })
        .filter((row): row is { lane: string; agent: Agent } => row !== null),
    [homeOpens, rail.rows, agents],
  );
  /** The lane the rail was last pressed for. The press places home and home brings the lane into
   *  view; a member who leaves home afterwards has been answered, so the seek does not outlive the
   *  screen and land again the next time home mounts. */
  const [seeking, setSeeking] = useState<Seek | undefined>(undefined);
  useEffect(() => {
    if (route.kind !== "home") setSeeking(undefined);
  }, [route.kind]);
  /** The lane home says the member is standing in, which the rail marks. The track is what knows it
   *  — the walk and the cursor both move it, and neither passes through the address — so it is held
   *  here rather than derived, and the track clears it as home unmounts. */
  const [activeLane, setActiveLane] = useState<string | undefined>(undefined);

  /** The router owns the address: it states the boot address in the bar, lands the arrival on the
   *  track the screen was left holding, and follows the browser from there. It starts here rather
   *  than while the shell renders, because it writes the address and the track store both. */
  useEffect(startRouter, []);

  useEffect(readRail, []);

  useProvisioned(agents, onAgents);

  /** The drawer stands over the page, so every act that moves the page shuts it. The router
   *  publishes a route for each of those acts, whether or not the address it wrote had changed. */
  useEffect(() => setMenu(false), [route]);

  /** A drawer left open while the window grows past the breakpoint would trap focus behind a
   *  hamburger the layout no longer draws. */
  useEffect(() => {
    if (!narrow) setMenu(false);
  }, [narrow]);

  useEffect(() => {
    document.title = pageTitle(route, agents, rail.rows, rail.linked);
  }, [route, agents, rail.rows, rail.linked]);

  // A workspace with no agent to talk to has no first run to stand in, so the member is sent to the
  // one screen they can act on.
  useEffect(() => {
    if (route.kind === "first-run" && !mainAgent) openHome();
  }, [mainAgent, route.kind]);

  /** A conversation the rail does not carry — a permalink to one another surface holds, or one past
   *  the rail's own bound — is resolved by a read of its own, once the rail has answered. */
  useEffect(() => {
    if (route.kind === "chat" && rail.phase === "ready") seekChat(route.conversationId);
  }, [route, rail]);

  /** The wizard mounts only behind a member's press or a run already in flight: its address alone
   *  must not found a conversation, or Back and reload would send model turns nobody asked for. */
  const [wantedBuild, setWantedBuild] = useState(false);
  const startBuild = useCallback(() => {
    setWantedBuild(true);
    openBuilder();
  }, []);
  const exitBuild = useCallback(() => {
    setWantedBuild(false);
    openApps();
  }, []);
  const forwardBuild = useCallback(() => {
    setWantedBuild(false);
    forwardApps();
  }, []);

  /** The first run draws no shell. It is the one destination a member reaches before the workspace
   *  is theirs to move around in, so the bar's four places are all somewhere they cannot use yet —
   *  and the page carries its own mark and its own foot instead. */
  if (route.kind === "first-run") {
    return (
      <WorkspaceId.Provider value={member.workspace_id ?? null}>
        <Viewer.Provider value={member.email}>
          <SurfacesProvider surfaces={surfaces}>
            <MainAgentProvider agents={agents}>
              {mainAgent ? (
                <FirstRun
                  agent={mainAgent}
                  agents={agents}
                  member={member}
                  onClose={() => openNewChat(mainAgent.id)}
                  onHandoff={(text) => {
                    /* Home stands the chat app's own lane, so the words name that lane and the
                       agent they address: an ask left for an agent's new chat screen is a screen
                       home never opens, and it would stand unread beside an empty box. The lane is
                       cleared first, so a member coming through the run again opens a conversation
                       of their own rather than the one the last run founded. */
                    const speaks = chatSurface(agents) ?? mainAgent;
                    const lane = mintHomeLane(speaks.id, []);
                    clearChat(lane);
                    setPendingAsk(speaks.id, text, true, lane);
                  }}
                  onDone={() => openHomeChat((chatSurface(agents) ?? mainAgent).id)}
                />
              ) : (
                <PaneNote>No such app.</PaneNote>
              )}
            </MainAgentProvider>
          </SurfacesProvider>
        </Viewer.Provider>
      </WorkspaceId.Provider>
    );
  }

  /** An app the workspace is still building draws no navigation either. Its setup screen is the one
   *  act it offers, and the column beside it names destinations the app is not one of yet — so the
   *  screen is the whole page until the setup ends.
   *
   *  The boot read carries the fact, so the app's own address answers it as surely as the setup
   *  address the pane replaces it with. Both are held before the first render: the sidebar is never
   *  drawn and then taken away, which is a column the member watches appear and vanish. A build
   *  that landed after that read is the pane's to find — it confirms before it moves the member,
   *  and the roster it re-reads is what stands this column back up. */
  const shell = !inSetup(route, agents);

  return (
    <WorkspaceId.Provider value={member.workspace_id ?? null}>
      <Viewer.Provider value={member.email}>
        <SurfacesProvider surfaces={surfaces}>
          <MainAgentProvider agents={agents}>
            <TooltipProvider>
            <Shortcuts />
            <DrawerHost hosted={narrow && shell} shut={shutMenu}>
              <div
                className={cn(
                  "grid h-dvh",
                  shell
                    ? "max-narrow:grid-cols-1 max-narrow:grid-rows-[auto_1fr] grid-cols-[var(--container-minirail)_1fr]"
                    : "max-narrow:grid-cols-1 grid-cols-[var(--container-minirail)_1fr]",
                )}
              >
                {shell && narrow ? (
                  <NarrowBar agents={listed} member={member} menu={menu} onMenu={setMenu} />
                ) : null}
                {shell && narrow ? (
                  <WorkspaceSidebar
                    route={route}
                    agents={listed}
                    member={member}
                    mainAgent={mainAgent}
                    onBuild={startBuild}
                  />
                ) : null}
                {/* The column opening the shell on the left, on every signed-in screen — an app's
                    setup page drops the wide navigation but keeps this rail, so the way to the other
                    apps never leaves. A phone width draws one column and the drawer holds what a
                    desk width puts beside the pane, so the rail is not drawn there rather than
                    stacked over the screen it stands beside. */}
                {!narrow ? (
                  <MinimalSidebar
                    lanes={homeLanes}
                    active={activeLane}
                    agents={listed}
                    account={<AccountMenu member={member} />}
                    onNewTab={() => {
                      setSeeking({ id: HOME_NEW_LANE, expansion: "restore" });
                      placeHome({ ...homePlace, opens: newTab(homeOpens) });
                    }}
                    onLane={(lane) => {
                      if (!homeOpens.includes(lane)) return;
                      setSeeking({ id: lane, expansion: "switch" });
                      placeHome({ ...homePlace, opens: homeOpens });
                    }}
                    onBuild={startBuild}
                  />
                ) : null}
                <AppsProvider agents={listed} archived={archived} onRestored={onAgents}>
                  <RoutedPane
                    route={route}
                    agents={agents}
                    member={member}
                    mainAgent={mainAgent}
                    seeking={seeking}
                    onActive={setActiveLane}
                    onAgents={onAgents}
                    onExitBuilder={exitBuild}
                    onForwardApps={forwardBuild}
                    buildWanted={wantedBuild}
                  />
                </AppsProvider>
                <Toast state={rail.fault ?? SILENT} onDone={quietRail} />
              </div>
            </DrawerHost>
            </TooltipProvider>
          </MainAgentProvider>
        </SurfacesProvider>
      </Viewer.Provider>
    </WorkspaceId.Provider>
  );
}

/** A conversation a send has just founded: the rail carries the row at once rather than waiting for
 *  its next read, and the screen that drew the unfounded chat hands the member to the conversation
 *  their message founded — a screen left standing on one would redraw an empty composer over what
 *  they just sent. */
function founded(agent: Agent, conversationId: string, title: string): void {
  railFounded({
    conversation_id: conversationId,
    agent_id: agent.id,
    agent_name: agent.name,
    title,
    last_at: stampIso(new Date()),
    surface: WEB_SURFACE,
    surface_label: null,
    mine: true,
    speaker: null,
  });
  const seen = heldRoute();
  if (seen.kind === "new-chat" && seen.agentId === agent.id) openChat(conversationId);
}

/** The bar a phone width keeps: the hamburger that opens the drawer holding the sidebar, the mark
 *  centred between it and the search and account closing the row. A desk width draws no bar at all
 *  — the sidebar is the shell. */
function NarrowBar({
  agents,
  member,
  menu,
  onMenu,
}: {
  agents: Agent[];
  member: Member;
  menu: boolean;
  onMenu: (open: boolean) => void;
}) {
  return (
    <header className="relative flex items-center gap-md border-b border-edge bg-sidebar px-lg py-md">
      <button
        type="button"
        aria-label="Menu"
        aria-expanded={menu}
        onClick={() => onMenu(true)}
        className="flex size-(--size-control) shrink-0 items-center justify-center rounded-full border-0 bg-transparent p-0 text-inherit hover:bg-fill"
      >
        <IconMenu2 className="size-(--size-glyph)" aria-hidden />
      </button>
      <button
        type="button"
        aria-label="ufo"
        onClick={openHome}
        className="absolute start-1/2 -translate-x-1/2 border-0 bg-transparent p-0 text-inherit rtl:translate-x-1/2"
      >
        <span
          role="img"
          aria-label="ufo"
          className="block h-(--size-wordmark) w-(--size-logo) bg-current"
          style={{ mask: `url(${logo}) center / contain no-repeat` }}
        />
      </button>
      <Spotlight
        agents={agents}
        className="ml-auto flex h-(--size-row) items-center rounded-full border-0 bg-transparent px-md text-inherit hover:bg-fill"
      />
      <AccountMenu member={member} />
      <NavDrawer open={menu} onClose={() => onMenu(false)} />
    </header>
  );
}

/** The sidebar at a phone width, where the page has no column for it: the same rows, held by the
 *  drawer the hamburger opens. */
function NavDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const hold = useDrawerSlot();
  return (
    <DialogPrimitive.Root open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-10 bg-scrim animate-appear" />
        <DialogPrimitive.Content
          data-slot="nav-drawer"
          aria-describedby={undefined}
          className={cn(
            "fixed inset-y-0 left-0 z-10 w-sidebar overflow-y-auto",
            "bg-sidebar border-r border-edge p-lg",
            "flex flex-col gap-2xl animate-slide-in",
          )}
        >
          <header className="flex h-(--size-control) shrink-0 items-center gap-md">
            <DialogPrimitive.Title asChild>
              <span
                role="img"
                aria-label="ufo"
                className="h-(--size-wordmark) w-(--size-logo) shrink-0 bg-current"
                style={{ mask: `url(${logo}) center / contain no-repeat` }}
              />
            </DialogPrimitive.Title>
            <DialogPrimitive.Close asChild>
              <Button size="icon" className="ml-auto" aria-label="Close">
                <IconX aria-hidden />
              </Button>
            </DialogPrimitive.Close>
          </header>
          <div ref={hold} className="flex min-h-0 flex-1 flex-col" />
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

/** The way back to the form, offered wherever the shell states who is signed in. The sign-in door
 *  forwards a browser that already holds a session, so signing in as another address — or into
 *  another workspace an invitation offered — starts by clearing this one. */
function signOut(): void {
  window.location.assign(SIGN_OUT_PATH);
}

/** The submenu trigger states the palette the member picked, not the one the browser resolved:
 *  `System` is a choice they can read back, and a value that flipped itself at dusk would say they
 *  had picked light. */
function AccountMenu({ member }: { member: Member }) {
  const scheme = useScheme();
  const surfaces = useSurfaces();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={member.email}
          className="flex shrink-0 items-center justify-center rounded-full border-0 bg-transparent p-0 max-narrow:size-(--size-control) data-[state=open]:outline data-[state=open]:outline-edge"
        >
          <Avatar>
            <AvatarFallback>{member.email.slice(0, 1).toUpperCase()}</AvatarFallback>
          </Avatar>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <div className="flex flex-col p-sm">
          <span className="truncate text-label">{member.email}</span>
          <span className="text-small text-ink-soft">{member.admin ? "Admin" : "Member"}</span>
        </div>
        <DropdownMenuSub>
          <DropdownMenuSubTrigger
            value={SCHEME_OPTIONS.find((option) => option.scheme === scheme)?.label}
          >
            Theme
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent>
            <DropdownMenuRadioGroup value={scheme} onValueChange={pickScheme}>
              {SCHEME_OPTIONS.map((option) => (
                <DropdownMenuRadioItem key={option.scheme} value={option.scheme}>
                  {option.label}
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuSubContent>
        </DropdownMenuSub>
        {member.admin && surfaces.admin ? (
          <DropdownMenuItem onSelect={openAdmin}>Administration</DropdownMenuItem>
        ) : null}
        <DropdownMenuItem onSelect={signOut}>Sign out</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

const APPS = "Apps";
const NEW_CHAT = "New chat";
const CREATE_APP = "Create app";

const NAV_ROW =
  "flex h-(--size-row) w-full items-center gap-md rounded-full border-0 bg-transparent px-sm text-left text-label text-inherit hover:bg-fill";

/** Which way a section's fold stands, stated by the chevron on its heading: down over an open
 *  section, along the row over a shut one. */
const SECTION_HEAD_CHEVRON =
  "size-icon shrink-0 transition-transform duration-100 ease-control motion-reduce:transition-none";

/** What stands pinned until the member pins for themselves: the chat app, and nothing else. Chat is
 *  where a member starts, so it holds the top of the column on a workspace nobody has arranged yet;
 *  every other app answers to the order the list already gives it. */
function defaultPins(agents: Agent[]): string[] {
  const chat = agents.find((agent) => agent.app === CHAT_SURFACE);
  return chat ? [chat.id] : [];
}

const GLYPH = "size-(--size-glyph) shrink-0";

const AskGlyph = () => <IconPlus className={GLYPH} aria-hidden />;
const CreateAppGlyph = () => <IconCirclePlus className={GLYPH} aria-hidden />;
const WorkspaceGlyph = () => <IconUsers className={GLYPH} aria-hidden />;

const SECTION_GLYPHS: Partial<Record<Section, React.ReactNode>> = {
  connectors: <IconPlug className={GLYPH} aria-hidden />,
};


function NavRow({
  icon,
  current,
  className,
  onClick,
  children,
}: {
  icon: React.ReactNode;
  current: boolean;
  className?: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-current={current}
      onClick={onClick}
      className={cn(NAV_ROW, current && "bg-fill", className)}
    >
      {icon}
      <span className="min-w-0 flex-1 truncate">{children}</span>
    </button>
  );
}

/** The glyph states the palette the member picked, not the one the browser resolved: `System` is a
 *  choice they can read back, and a sun that flips itself at dusk would say they had picked light. */
function SchemeGlyph({ scheme }: { scheme: Scheme }) {
  if (scheme === "light") return <IconSun className={GLYPH} aria-hidden />;
  if (scheme === "dark") return <IconMoon className={GLYPH} aria-hidden />;
  return <IconDeviceDesktop className={GLYPH} aria-hidden />;
}

function SchemePick() {
  const scheme = useScheme();
  return (
    <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            aria-label="Theme"
            className="rounded-control border-0 bg-transparent p-2xs text-ink-soft hover:bg-fill data-[state=open]:bg-fill"
          >
            <SchemeGlyph scheme={scheme} />
          </button>
        </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuRadioGroup value={scheme} onValueChange={pickScheme}>
          {SCHEME_OPTIONS.map((option) => (
            <DropdownMenuRadioItem key={option.scheme} value={option.scheme}>
              {option.label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** A sidebar section's head: the band is the section's name and the whole of it is the fold, because
 *  putting a section away is the thing a member does to a heading. A chevron states which way that
 *  fold stands — pointing down over an open section, along the row over a shut one — so the band
 *  answers "is my column hiding anything" without being clicked. */
function SectionHead({
  label,
  shut,
  onShut,
}: {
  label: string;
  shut: boolean;
  onShut: (shut: boolean) => void;
}) {
  return (
    <div
      className="group/head flex h-(--size-row) w-full shrink-0 items-center rounded-control hover:bg-fill"
    >
      <button
        type="button"
        aria-expanded={!shut}
        onClick={() => onShut(!shut)}
        className={cn(
          "flex min-w-0 flex-1 items-center gap-sm border-0 bg-transparent px-sm",
          "text-left font-sans text-label font-medium text-ink-soft",
        )}
      >
        <span className="min-w-0 truncate">{label}</span>
        <IconChevronDown className={cn(SECTION_HEAD_CHEVRON, shut && "-rotate-90")} aria-hidden />
      </button>
    </div>
  );
}

/** The shell's one nav: the mark and its fold control, search, the new-conversation act, the
 *  Applications section with the cross-app reads under it, and the workspace-wide destinations at
 *  the foot. It is the way to another screen and nothing else — a member finds a conversation by
 *  name on the chat app's own page, which is the screen that lists them. At a desk width it is the
 *  left column, folding to a glyph rail; at a phone width the drawer holds it and the fold is
 *  ignored, because a drawer is always drawn whole. */
function WorkspaceSidebar({
  route,
  agents,
  member,
  mainAgent,
  onBuild,
}: {
  route: Route;
  agents: Agent[];
  member: Member;
  mainAgent: Agent | null;
  onBuild: () => void;
}) {
  const rail = useRail();
  const surfaces = useSurfaces();
  const appsShut = rail.sectionsShut.includes(APPS);
  const pinned = rail.pinned ?? defaultPins(agents);
  const chatApp = chatSurface(agents);
  return useDrawerList(
    <nav
      aria-label="Workspace"
      className={cn(
        "flex min-h-0 flex-col gap-sm border-r border-edge bg-sidebar py-xl",
        "max-narrow:flex-1 max-narrow:border-r-0 max-narrow:py-0",
      )}
    >
      {/* The two acts the shell carries, above the places it reaches: starting a conversation and
          building an app are the things a member does here rather than screens they go to, and every
          member is offered both — the `agent` kind admits a create from any speaking member and
          stamps them the owner, and the wizard rides the main agent's own chat, so a workspace with
          no main agent offers neither. */}
      <ul className="m-0 flex list-none flex-col gap-px px-sm py-0">
        {mainAgent ? (
          <>
            <li>
              <NavRow
                icon={<AskGlyph />}
                current={standing(route, COMPOSING)}
                onClick={() =>
                  chatApp
                    ? openAgentPlace(chatApp.id, { opens: [COMPOSE] })
                    : openNewChat(mainAgent.id)
                }
              >
                {NEW_CHAT}
              </NavRow>
            </li>
            <li>
              <NavRow
                icon={<CreateAppGlyph />}
                current={route.kind === "builder"}
                onClick={onBuild}
              >
                {CREATE_APP}
              </NavRow>
            </li>
          </>
        ) : null}
      </ul>
      {/* The workspace's apps, drawn where the member works rather than behind a hover: the column
          states what each one is doing, which is the fact the sidebar exists to carry. Pinned rows
          lead, the apps that worked lately follow, and the rest wait behind `More`. */}
      {/* The section yields before the shell does. Held at its natural height it would stand a
          full sixteen rows tall on a screen with no room for them, and the nav scrolls nowhere —
          so the foot of the sidebar, and the way out of it, would be pushed off the bottom edge.
          Shrinking here spends the squeeze inside the list, which already scrolls. */}
      <div className="flex min-h-0 flex-col gap-px px-sm">
        <SectionHead
          label={APPS}
          shut={appsShut}
          onShut={(shut) => pickSectionShut(APPS, shut)}
        />
        {appsShut ? null : (
        <AppsIndex
          agents={agents}
          openId={route.kind === "agent" ? route.agentId : null}
          building={route.kind === "builder"}
          pinned={pinned}
          onPin={(agentId) =>
            pickPinned(
              pinned.includes(agentId)
                ? pinned.filter((id) => id !== agentId)
                : [...pinned, agentId],
            )
          }
          onOpen={openAgent}
          onBuild={onBuild}
        />
        )}
      </div>
      <ul className="m-0 mt-auto flex shrink-0 list-none flex-col gap-px px-sm py-0">
        <li>
          <NavRow
            icon={SECTION_GLYPHS.connectors}
            current={standing(route, "section:connectors")}
            onClick={() => placeSection("connectors", {}, "push")}
          >
            {CONNECTORS.label}
          </NavRow>
        </li>
        <li>
          <NavRow
            icon={<WorkspaceGlyph />}
            current={standing(route, "workspace")}
            onClick={() => placeWorkspace("team", {}, "push")}
          >
            Workspace
          </NavRow>
        </li>
      </ul>
      <footer
        className="flex shrink-0 items-center gap-sm px-lg"
      >
          <Avatar>
            <AvatarFallback>{member.email.slice(0, 1).toUpperCase()}</AvatarFallback>
          </Avatar>
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="truncate text-label">{member.email}</span>
          <span className="text-small text-ink-soft">{member.admin ? "Admin" : "Member"}</span>
        </span>
        <SchemePick />
        {member.admin && surfaces.admin ? (
            <button
              type="button"
              aria-label="Administration"
              onClick={openAdmin}
              className="rounded-control border-0 bg-transparent p-2xs text-ink-soft hover:bg-fill"
            >
              <IconSettings className={GLYPH} aria-hidden />
            </button>
        ) : null}
          <button
            type="button"
            aria-label="Sign out"
            onClick={signOut}
            className="rounded-control border-0 bg-transparent p-2xs text-ink-soft hover:bg-fill"
          >
            <IconLogout className={GLYPH} aria-hidden />
          </button>
      </footer>
    </nav>,
  );
}

/** A section address whose screen ships as an app: the name outlives who renders it, so the
 *  address lands on that app with its place carried rather than dying as a bad link. */
function SectionLanding({ agentId, place }: { agentId: string; place: WorkspacePlace }) {
  useEffect(() => openAgentPlace(agentId, place), [agentId, place]);
  return null;
}

/** The workspace tabs this deploy draws. A withheld screen loses its tab and keeps its address, so
 *  a member holding the link still lands on it; the skills tab stands while either of its two
 *  panels does, and goes when neither is offered. */
function offeredTabs(surfaces: Surfaces): readonly WorkspaceTab[] {
  return WORKSPACE_TABS.filter((tab) =>
    tab === "memory"
      ? surfaces.memory
      : tab === "skills"
        ? surfaces["community-skills"] || surfaces["installed-skills"]
        : true,
  );
}

function RoutedPane({
  route,
  agents,
  member,
  mainAgent,
  seeking,
  onActive,
  onAgents,
  onExitBuilder,
  onForwardApps,
  buildWanted,
}: {
  route: Route;
  agents: Agent[];
  member: Member;
  mainAgent: Agent | null;
  seeking: Seek | undefined;
  onActive: (lane: string | undefined) => void;
  onAgents: () => void;
  onExitBuilder: () => void;
  onForwardApps: () => void;
  buildWanted: boolean;
}) {
  const rail = useRail();
  const surfaces = useSurfaces();
  /** Where the member came from, off the trail that makes the tab title: every band on this screen
   *  names the trail's innermost step, so they all draw this one step over it and none of them
   *  derives it a second time. */
  const crumb = pageCrumb(route, agents, rail.rows, rail.linked);
  switch (route.kind) {
    case "admin":
      return <Admin />;
    case "agent-setup": {
      const app = agents.find((entry) => entry.id === route.agentId) ?? null;
      if (!app) return <PaneNote>No such app.</PaneNote>;
      return (
        <Pane>
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            <Header crumb={crumb} title={SETUP} pinned />
            <div className={cn(COLUMN, "flex-1 overflow-y-auto p-2xl")}>
              <AgentSetup agent={app} admin={member.admin} onBuilt={onAgents} />
            </div>
          </div>
        </Pane>
      );
    }
    case "bad-link":
      return <PaneNote>This link is not valid.</PaneNote>;
    case "workspace":
      return (
        <TabbedPane
          group="workspace"
          tabs={offeredTabs(surfaces)}
          views={WORKSPACE_VIEWS}
          view={route.view}
          crumb={crumb}
          place={route.place}
          onPlace={placeWorkspace}
        />
      );
    case "section": {
      const view = SECTION_VIEWS[route.section];
      if (view === undefined) {
        const shipped = agents.find((agent) => agent.app === route.section);
        if (!shipped) return <PaneNote>This link is not valid.</PaneNote>;
        return <SectionLanding agentId={shipped.id} place={route.place} />;
      }
      return (
        <TabbedPane
          group="section"
          tabs={[route.section]}
          views={{ [route.section]: view } as Record<Section, PaneView>}
          view={route.section}
          crumb={crumb}
          place={route.place}
          onPlace={placeSection}
        />
      );
    }
    case "builder":
      return (
        <Pane>
          <AgentBuilder
            member={member}
            onAgents={onAgents}
            onExitBuilder={onExitBuilder}
            onForwardApps={onForwardApps}
            buildWanted={buildWanted}
          />
        </Pane>
      );
    case "agent": {
      const selected = agents.find((entry) => entry.id === route.agentId);
      if (!selected) return <PaneNote>No such app.</PaneNote>;
      return (
        <Pane
          opens={route.place.opens ?? []}
          onMove={(opens) => placeAgent({ ...route.place, opens }, "replace")}
        >
          <Agents
            member={member}
            selected={selected}
            chats={rail.phase === "ready" ? rail.rows : null}
            onCreated={founded}
            place={route.place}
            onPlace={placeAgent}
            onAgents={onAgents}
          />
        </Pane>
      );
    }
    case "conversation-slot": {
      const agent = agents.find((entry) => entry.id === route.agentId);
      if (!agent) return <PaneNote>No such app.</PaneNote>;
      return (
        <ConversationSlotPane
          agent={agent}
          conversationId={route.conversationId}
          slot={route.slot}
          rootConversationId={route.rootConversationId}
          crumb={crumb}
        />
      );
    }
    case "chat": {
      const row = rail.rows.find(
        (entry) => entry.conversation_id === route.conversationId && isPortalChat(entry.surface),
      );
      const linkedConversation = rail.linked[route.conversationId];
      if (!row && linkedConversation) {
        const linkedAgent = agents.find((entry) => entry.id === linkedConversation.agent.id);
        if (!linkedAgent) return <PaneNote>No such app.</PaneNote>;
        if (!linkedConversation.readable && !linkedConversation.disclosable) return <NotShared />;
        return (
          <LinkedPane
            key={linkedConversation.id}
            agent={linkedAgent}
            conversation={linkedConversation}
            member={member}
            crumb={crumb}
            slot={route.slot}
            onActivity={railActivity}
            onSelectSlot={(slot) => openSlot(route.conversationId, slot)}
          />
        );
      }
      const listedAgent = row ? agents.find((entry) => entry.id === row.agent_id) : undefined;
      const agent =
        listedAgent ??
        (row && row.surface.startsWith("extension:")
          ? { id: row.agent_id, name: row.agent_name, model: "" }
          : undefined);
      if (!row || !agent) {
        if (rail.phase === "loading")
          return (
            <PaneNote>
              <Waiting />
            </PaneNote>
          );
        if (rail.phase === "failed") return <PaneNote>Couldn't load conversations.</PaneNote>;
        const outcome = rail.sought[route.conversationId];
        if (!outcome)
          return (
            <PaneNote>
              <Waiting />
            </PaneNote>
          );
        if (outcome.kind === "signed-out") {
          return (
            <Pane className={COLUMN}>
              <div className="m-auto">
                <SignIn />
              </div>
            </Pane>
          );
        }
        if (outcome.kind === "failed") return <PaneNote>{outcome.message}</PaneNote>;
        return <NotShared />;
      }
      return (
        <ChatPane
          key={row.conversation_id}
          agent={agent}
          member={member}
          conversationId={row.conversation_id}
          onActivity={railActivity}
          title={row.title}
          conversationOnly={!listedAgent}
          crumb={crumb}
          slot={route.slot}
          onSelectSlot={(slot) => openSlot(route.conversationId, slot)}
        />
      );
    }
    /* The shell draws the first run itself, above this dispatch, and sends a workspace with no main
       agent home, so `first-run` reaches here on neither path. It is answered all the same, because
       every kind the table declares is answered here or the switch does not compile. */
    case "home":
      return (
        <Home
          place={route.place}
          agents={agents}
          member={member}
          mainAgent={mainAgent}
          seeking={seeking}
          onActive={onActive}
          onFounded={founded}
          onActivity={railActivity}
          onAgents={onAgents}
        />
      );
    /* The shell draws the first run itself, above this dispatch, and sends a workspace with no main
       agent home, so `first-run` reaches here on neither path. It is answered all the same, because
       every kind the table declares is answered here or the switch does not compile. */
    case "first-run":
    case "new-chat": {
      const agent =
        route.kind === "new-chat"
          ? agents.find((entry) => entry.id === route.agentId)
          : (mainAgent ?? undefined);
      if (!agent) return <PaneNote>No such app.</PaneNote>;
      // One start screen, whichever agent it names. A route naming another agent renames the one
      // this screen stands for, and a key carrying that agent would remount the box on every
      // rename — a fresh box holds the draft again but not the member's place in it, and the
      // cursor lands back at the first character. The chat state and the draft are keyed by the
      // agent inside it instead, and its composer takes a pending ask on the agent it is renamed
      // to, because no mount comes to read one handed to the agent the route has just named.
      return (
        <ChatPane
          key="new"
          agent={agent}
          member={member}
          conversationId={null}
          onCreated={(conversationId, title) => founded(agent, conversationId, title)}
          onActivity={railActivity}
        />
      );
    }
  }
  const missed: never = route;
  return missed;
}

/** A conversation another surface holds, read in the portal: the thread pane a portal chat wears,
 *  headed the same way — the agent holding it and what it is called — with the
 *  surface it is happening on marked at the far end of that header, as the way out to it. The
 *  header states the name once, so the transcript under it draws no heading of its own. */
function LinkedPane({
  agent,
  conversation,
  member,
  crumb,
  slot,
  onActivity,
  onSelectSlot,
}: {
  agent: Agent;
  conversation: OwnedConversation;
  member: Member;
  crumb?: Crumb;
  slot?: string;
  onActivity: (conversationId: string) => void;
  onSelectSlot: (slot: string | null) => void;
}) {
  const [disclosed, setDisclosed] = useState(false);
  const viewer = useViewer();
  const readable = conversation.readable || disclosed;
  return (
    <Pane>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <Header
          crumb={crumb}
          title={subject(conversation, viewer)}
          acts={<SurfaceMark conversation={conversation} />}
          pinned
        />
        {readable ? (
          conversation.commentable ? (
            <Chat
              agent={agent}
              member={member}
              conversationId={conversation.id}
              onActivity={onActivity}
            />
          ) : (
            <div className={cn(COLUMN, "flex-1 overflow-y-auto p-2xl")} data-testid="panel">
              <ConversationDetail
                agent={agent}
                conversation={conversation}
                headed
              />
              <p className="max-w-hint text-ink-soft">
                This conversation is read-only here. Reply in {surfaceWord(conversation.surface)} to
                continue it.
              </p>
            </div>
          )
        ) : (
          <div className={cn(COLUMN, "flex-1 overflow-y-auto p-2xl")} data-testid="panel">
            <Disclose
              agent={agent}
              conversation={conversation}
              onOpened={() => setDisclosed(true)}
            />
          </div>
        )}
      </div>
      {readable && slot ? (
        <ConversationSlot
          agent={agent}
          conversationId={conversation.id}
          slot={slot}
          onClose={() => onSelectSlot(null)}
        />
      ) : null}
    </Pane>
  );
}

function NotShared() {
  return <PaneNote>This conversation is not shared with this account.</PaneNote>;
}

export const SCROLL_MARK = "data-scrolling";

/** How long a stopped pane keeps its thumb. It covers the pause between two wheel notches and the
 *  pause in the middle of a drag, so one gesture draws one bar rather than a blinking one, and it
 *  is short enough that a pane the member has left alone is quiet before their eye comes back. */
export const SCROLL_QUIET_MS = 600;

/** Writes `theme.css`'s scroll mark on whatever is moving, so the thumb is drawn while the member
 *  scrolls and at no other time. `scroll` does not bubble but it does capture, so one listener at
 *  the document reaches every scroller the portal draws, including one mounted after this ran.
 *  Each element carries its own quiet timer — a pane still moving keeps its bar while a pane that
 *  has stopped loses one — and the mark is written once per gesture rather than once per event,
 *  since a scroll fires every frame and the attribute already says what the next frame would. */
function useScrollMark(): void {
  useEffect(() => {
    const quiet = new Map<Element, number>();
    const mark = (event: Event) => {
      const element = event.target;
      if (!(element instanceof Element)) return;
      const held = quiet.get(element);
      if (held === undefined) element.setAttribute(SCROLL_MARK, "");
      else clearTimeout(held);
      quiet.set(
        element,
        window.setTimeout(() => {
          quiet.delete(element);
          element.removeAttribute(SCROLL_MARK);
        }, SCROLL_QUIET_MS),
      );
    };
    document.addEventListener("scroll", mark, { capture: true, passive: true });
    return () => {
      document.removeEventListener("scroll", mark, { capture: true });
      for (const [element, held] of quiet) {
        clearTimeout(held);
        element.removeAttribute(SCROLL_MARK);
      }
    };
  }, []);
}
