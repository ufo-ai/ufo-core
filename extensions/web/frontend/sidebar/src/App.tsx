import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import {
  IconBrandSlack,
  IconBroadcast,
  IconChevronDown,
  IconFilter2,
  IconChevronRight,
  IconDeviceDesktop,
  IconLayoutSidebarRight,
  IconLogout,
  IconMenu2,
  IconMessageCircle,
  IconMoon,
  IconPlug,
  IconPlus,
  IconSun,
  IconTerminal2,
  IconUsers,
  IconX,
} from "@tabler/icons-react";

import logo from "@/assets/ufo-logo.svg";
import { SidebarPress, SidebarRow } from "@/components/Sidebar";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Ticker } from "@/components/ui/ticker";
import { SILENT, Toast } from "@/components/ui/toast";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { AgentSetup } from "@/views/AgentSetup";

import { Agents, AppsIndex } from "@/views/Agents";
import { AppsProvider } from "@/views/Apps";
import { Chat } from "@/views/Chat";
import { ChatPane, ConversationSlot } from "@/views/ChatPane";
import { ConversationSlotPane } from "@/views/ConversationSlotPane";
import { ConversationDetail, Disclose, subject } from "@/views/Conversations";
import { FirstRun } from "@/views/FirstRun";
import { SignIn } from "@/views/SignIn";
import { ConnectSurfaces, SURFACES_READ, type SurfacesPayload } from "@/views/Surfaces";
import { Spotlight } from "@/views/Spotlight";
import { TabbedPane } from "@/views/TabbedPane";
import { CONNECTORS, SECTION_VIEWS, WORKSPACE_VIEWS, type PaneView } from "@/views/registry";
import {
  IMESSAGE_SURFACE,
  SLACK_SURFACE,
  UFO_SURFACE,
  WEB_SURFACE,
  isPortalChat,
  Viewer,
  WorkspaceId,
  origin,
  slackLink,
  speakerName,
  surfaceWord,
  useViewer,
} from "@/lib/audience";
import { SIGN_OUT_PATH } from "@/lib/api";
import { useAppStatus } from "@/lib/appStatusStore";
import { DrawerHost, useDrawerHost, useDrawerList, useDrawerSlot } from "@/kernel/drawer";
import { COLUMN, Header, Pane, PaneNote } from "@/kernel/pane";
import { Loading, usePanelRead } from "@/kernel/panel";
import { agentName } from "@/lib/agentName";
import { CHAT_SURFACE, MainAgentProvider, chatSurface } from "@/lib/mainAgent";
import { cn } from "@/lib/cn";
import { SCHEME_OPTIONS, pickScheme, useScheme, type Scheme } from "@/lib/scheme";
import { SETUP, pageCrumb, pageTitle, type Crumb } from "@/lib/title";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { RAIL_SHOWN_OPTIONS, railGroups, railShut, stampIso, type RailGroup } from "@/lib/rail";
import {
  foldSidebar,
  pickAppsExpanded,
  pickPinned,
  pickSectionShut,
  pickRailShown,
  pickRailShut,
  pickRailSort,
  quietRail,
  railActivity,
  railFounded,
  readRail,
  seekChat,
  useRail,
} from "@/lib/railStore";
import {
  forwardAgents,
  heldRoute,
  openAgent,
  openAgentPlace,
  openAgents,
  openBuilder,
  openChat,
  openHome,
  openNewChat,
  openSlot,
  placeAgent,
  placeFirstRun,
  placeSection,
  placeWorkspace,
  startRouter,
  useRoute,
} from "@/lib/router";
import {
  COMPOSE,
  COMPOSING,
  standing,
  type Route,
  type Section,
  type WorkspacePlace,
} from "@/lib/route";
import { ALL_SURFACES, SurfacesProvider, useOfferedTabs } from "@/lib/surfaces";
import type { Agent, ArchivedApp, Member, OwnedConversation, Surfaces } from "@/lib/types";

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
   *  from its address, and still answers a picker — it is kept out of the rail, the flyout and the
   *  apps listing, which is the whole of what hiding one means. `mainAgent` reads the whole set
   *  above, so withholding the assistant costs the composer and the first run nothing. */
  const listed = agents.filter((agent) => !agent.hidden);

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
    document.title = pageTitle(route, agents, rail.rows, rail.linked, mainAgent);
  }, [route, agents, rail.rows, rail.linked, mainAgent]);

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
    openAgents();
  }, []);
  const forwardBuild = useCallback(() => {
    setWantedBuild(false);
    forwardAgents();
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
                step={route.step}
                onStep={placeFirstRun}
                onOpenChat={(conversationId) =>
                  conversationId ? openChat(conversationId) : openNewChat(mainAgent.id)
                }
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
          <DrawerHost hosted={narrow && shell} shut={shutMenu}>
            <div
              className={cn(
                "grid h-dvh",
                shell
                  ? cn(
                      "max-narrow:grid-cols-1 max-narrow:grid-rows-[auto_1fr]",
                      rail.collapsed
                        ? "grid-cols-[var(--container-rail)_1fr]"
                        : "grid-cols-[var(--container-sidebar)_1fr]",
                    )
                  : "grid-cols-1",
              )}
            >
              {shell && narrow ? (
                <NarrowBar agents={listed} member={member} menu={menu} onMenu={setMenu} />
              ) : null}
              {shell ? (
                <WorkspaceSidebar
                  route={route}
                  agents={listed}
                  member={member}
                  mainAgent={mainAgent}
                  narrow={narrow}
                  onBuild={startBuild}
                />
              ) : null}
              <AppsProvider agents={listed} archived={archived} onRestored={onAgents}>
                <RoutedPane
                  route={route}
                  agents={agents}
                  member={member}
                  mainAgent={mainAgent}
                  onAgents={onAgents}
                  onExitBuilder={exitBuild}
                  onForwardAgents={forwardBuild}
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
  if (seen.kind === "home" || (seen.kind === "new-chat" && seen.agentId === agent.id)) {
    openChat(conversationId);
  }
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
        <DropdownMenuItem onSelect={signOut}>Sign out</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

const APPS = "Apps";
const CHATS = "Chats";
const CHANNELS = "Channels";

/** A sidebar section's heading — the muted band the whole line of which opens the section's menu.
 *  The glyph that states the menu is drawn only once the section is pointed at, reached by keyboard,
 *  or standing open: a column of headings each carrying a control the member is not using reads as
 *  a toolbar, and the heading is a place before it is an act. It holds its box while hidden, so
 *  nothing under the pointer moves. */
const SECTION_HEAD_CHEVRON =
  "size-icon shrink-0 transition-transform duration-100 ease-control motion-reduce:transition-none";

/** The menu the section holds, drawn only once the band is pointed at, reached by keyboard, or
 *  standing open: a column of headings each carrying a control the member is not using reads as a
 *  toolbar, and the heading is a place before it is an act. It holds its box while hidden, so
 *  nothing under the pointer moves. */
const SECTION_HEAD_GLYPH =
  "mr-xs shrink-0 rounded-control border-0 bg-transparent p-2xs text-ink-soft hover:bg-fill opacity-0 transition-opacity duration-100 ease-control motion-reduce:transition-none group-hover/head:opacity-100 focus-visible:opacity-100 data-[state=open]:opacity-100 data-[state=open]:bg-fill";

/** The rows the sidebar pins where the member has pinned none themselves: the workspace's shipped
 *  apps, in name order, so the sidebar arrives holding its own destinations. The chat app is not one
 *  of them — the Ask assistant row above is the way to it. */
function defaultPins(agents: Agent[]): string[] {
  const apps = agents.filter((agent) => agent.app && agent.app !== CHAT_SURFACE);
  apps.sort((a, b) => a.name.localeCompare(b.name));
  return apps.map((agent) => agent.id);
}

const GLYPH = "size-(--size-glyph) shrink-0";

const AskGlyph = () => <IconPlus className={GLYPH} aria-hidden />;
const WorkspaceGlyph = () => <IconUsers className={GLYPH} aria-hidden />;

const SECTION_GLYPHS: Partial<Record<Section, React.ReactNode>> = {
  connectors: <IconPlug className={GLYPH} aria-hidden />,
};

function SidebarTooltip({
  collapsed,
  label,
  children,
}: {
  collapsed: boolean;
  label: string;
  children: React.ReactElement;
}) {
  if (!collapsed) return children;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}

/** The two controls the sidebar header carries beside the mark. Their glyphs stand 8px apart and
 *  12px in from the sidebar's edge, on the pitch the rows under them keep. */
const HEADER_CONTROL = "rounded-control border-0 bg-transparent p-2xs text-ink-soft hover:bg-fill";

function SidebarToggle({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button type="button" aria-label={label} onClick={onClick} className={HEADER_CONTROL}>
      <IconLayoutSidebarRight className={GLYPH} aria-hidden />
    </button>
  );
}

/** A destination the column stands on its own: drawn as every other row in the sidebar is, so the
 *  workspace's own places and the lists between them read as one column. */
function NavRow({
  icon,
  current,
  collapsed,
  label,
  onClick,
}: {
  icon: React.ReactNode;
  current: boolean;
  collapsed: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <SidebarRow current={current}>
      <SidebarTooltip collapsed={collapsed} label={label}>
        <SidebarPress
          current={current}
          collapsed={collapsed}
          label={label}
          glyph={icon}
          onClick={onClick}
        />
      </SidebarTooltip>
    </SidebarRow>
  );
}

/** The glyph states the palette the member picked, not the one the browser resolved: `System` is a
 *  choice they can read back, and a sun that flips itself at dusk would say they had picked light. */
function SchemeGlyph({ scheme }: { scheme: Scheme }) {
  if (scheme === "light") return <IconSun className={GLYPH} aria-hidden />;
  if (scheme === "dark") return <IconMoon className={GLYPH} aria-hidden />;
  return <IconDeviceDesktop className={GLYPH} aria-hidden />;
}

function SchemePick({ collapsed }: { collapsed: boolean }) {
  const scheme = useScheme();
  return (
    <DropdownMenu>
      <SidebarTooltip collapsed={collapsed} label="Theme">
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            aria-label="Theme"
            className="rounded-control border-0 bg-transparent p-2xs text-ink-soft hover:bg-fill data-[state=open]:bg-fill"
          >
            <SchemeGlyph scheme={scheme} />
          </button>
        </DropdownMenuTrigger>
      </SidebarTooltip>
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

/** The channels a member reaches the workspace from, held behind the foot of the sidebar: the dot
 *  states that one of the three is still unconnected, and the dialog is where they connect it. The
 *  read stands at the shell's own cadence and is taken again when the dialog closes, which is when
 *  a connect made inside it has landed. A row this deploy does not offer is no missing channel. */
function ChannelsPick({
  collapsed,
  agent,
  member,
}: {
  collapsed: boolean;
  agent: Agent | null;
  member: Member;
}) {
  const [open, setOpen] = useState(false);
  const [toast, setToast] = useState(SILENT);
  const [reloads, setReloads] = useState(0);
  const state = usePanelRead<SurfacesPayload>(SURFACES_READ, reloads);
  const refuse = useCallback((title: string) => setToast({ title }), []);
  const missing =
    state.phase === "ready" &&
    state.payload.surfaces.some((row) => row.offered && !row.connected);
  return (
    <>
      <SidebarTooltip collapsed={collapsed} label={CHANNELS}>
        <button
          type="button"
          aria-label={CHANNELS}
          onClick={() => setOpen(true)}
          className="relative rounded-control border-0 bg-transparent p-2xs text-ink-soft hover:bg-fill"
        >
          <IconBroadcast className={GLYPH} aria-hidden />
          {missing ? (
            <span
              aria-hidden
              className="absolute -right-2xs -bottom-2xs size-sm rounded-full bg-attention-ink"
            />
          ) : null}
        </button>
      </SidebarTooltip>
      {open && agent ? (
          <Dialog
            open
            onOpenChange={(next) => {
              if (next) return;
              setOpen(false);
              setReloads((count) => count + 1);
            }}
          >
          <DialogContent>
            <DialogHeader className="flex-row items-center justify-between">
              <DialogTitle>{CHANNELS}</DialogTitle>
              <DialogPrimitive.Close asChild>
                <Button variant="mark" size="glyph" aria-label="Close" className="text-ink-quiet">
                  <IconX stroke={1.25} aria-hidden />
                </Button>
              </DialogPrimitive.Close>
            </DialogHeader>
            <ConnectSurfaces
              agent={agent}
              member={member}
              onRefused={refuse}
            />
          </DialogContent>
        </Dialog>
      ) : null}
      <Toast state={toast} onDone={() => setToast(SILENT)} />
    </>
  );
}

/** A sidebar section's head: the band folds the section, and the mark at its end opens the menu.
 *
 *  Two acts, two controls. The band is the section's name and the whole of it is the fold, because
 *  putting a section away is the thing a member does to a heading; the menu is a second act on the
 *  same row and gets its own mark rather than stealing the first. A chevron states which way the
 *  fold stands — pointing down over an open section, along the row over a shut one — so the band
 *  answers "is my column hiding anything" without being clicked.
 *
 *  The two are siblings rather than nested, because a control inside a control is neither. */
function SectionHead({
  label,
  shut,
  collapsed,
  onShut,
  children,
}: {
  label: string;
  shut: boolean;
  /** Whether the sidebar stands folded to its glyph rail, which has no room for a section's name. */
  collapsed?: boolean;
  onShut: (shut: boolean) => void;
  /** The menu the band's mark opens. A section with nothing to offer beyond its own rows passes
   *  none, and draws no mark: a control that opens an empty menu is a control that lies. */
  children?: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "group/head flex h-(--size-row) w-full shrink-0 items-center rounded-control hover:bg-fill",
        collapsed && "hidden",
      )}
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
      {children ? (
        <DropdownMenu modal={false}>
          <DropdownMenuTrigger asChild>
            <button type="button" aria-label={label + " options"} className={SECTION_HEAD_GLYPH}>
              <IconFilter2 className="size-icon" aria-hidden />
            </button>
          </DropdownMenuTrigger>
          {children}
        </DropdownMenu>
      ) : null}
    </div>
  );
}

/** The shell's one nav: the mark and its fold control, search, the new-conversation act, the
 *  Applications section with the cross-app reads under it, the conversations rail, and the
 *  workspace-wide destinations at the foot. At a desk width it is the left column, folding to a
 *  glyph rail; at a phone width the drawer holds it and the fold is ignored, because a drawer is
 *  always drawn whole. */
function WorkspaceSidebar({
  route,
  agents,
  member,
  mainAgent,
  narrow,
  onBuild,
}: {
  route: Route;
  agents: Agent[];
  member: Member;
  mainAgent: Agent | null;
  narrow: boolean;
  onBuild: () => void;
}) {
  const rail = useRail();
  const tabs = useOfferedTabs();
  /* A drawer is always drawn whole, so the fold a desk width holds is ignored while it stands. */
  const collapsed = rail.collapsed && !narrow;
  /* A folded section states nothing and its head is what opens it again — so on the glyph rail,
     where no head is drawn, the fold is ignored rather than leaving rows nobody can reach. */
  const appsShut = !collapsed && rail.sectionsShut.includes(APPS);
  const chatsShut = !collapsed && rail.sectionsShut.includes(CHATS);
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
      <div
        className={cn(
          "flex shrink-0 items-center max-narrow:hidden",
          collapsed
            ? "flex-col justify-center px-sm"
            : "h-(--size-row) justify-between pl-2xl pr-sm",
        )}
      >
        <span
          role="img"
          aria-label="ufo"
          className={cn("h-(--size-wordmark) w-(--size-logo) bg-current", collapsed && "hidden")}
          style={{ mask: `url(${logo}) center / contain no-repeat` }}
        />
        <span className={cn("flex items-center", collapsed && "flex-col")}>
          <Spotlight agents={agents} className={HEADER_CONTROL} />
          <SidebarTooltip collapsed={collapsed} label="Expand sidebar">
            <SidebarToggle
              label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              onClick={() => foldSidebar(!collapsed)}
            />
          </SidebarTooltip>
        </span>
      </div>
      <ul className="m-0 flex list-none flex-col gap-px px-sm py-0">
        {mainAgent ? (
          <NavRow
            icon={<AskGlyph />}
            current={standing(route, COMPOSING)}
            collapsed={collapsed}
            label="Ask assistant"
            onClick={() =>
              chatApp
                ? openAgentPlace(chatApp.id, { opens: [COMPOSE] })
                : openNewChat(mainAgent.id)
            }
          />
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
          collapsed={collapsed}
          onShut={(shut) => pickSectionShut(APPS, shut)}
        />
        {appsShut ? null : (
        <AppsIndex
          agents={agents}
          openId={route.kind === "agent" ? route.agentId : null}
          building={route.kind === "agents" && route.build === true}
          pinned={pinned}
          expanded={rail.appsExpanded}
          collapsed={collapsed}
          onExpand={pickAppsExpanded}
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
      {/* The conversations section is built the way the applications section above it is: the
          heading and what stands under it are one column, so a section's first row sits the same
          hair below its heading in both. Only the rows scroll — a heading that scrolled away would
          leave the filter it carries unreachable at the foot of a long rail. */}
      <div className={cn("flex min-h-0 flex-1 flex-col gap-px px-sm", collapsed && "hidden")}>
        <RailSettingsFlyout shut={chatsShut} onShut={(shut) => pickSectionShut(CHATS, shut)} />
        {chatsShut ? null : (
          <div className="flex min-h-0 flex-1 flex-col gap-sm overflow-y-auto">
            <RailList route={route} agents={agents} mainAgent={mainAgent} chatApp={chatApp} />
          </div>
        )}
      </div>
      <ul className="m-0 mt-auto flex shrink-0 list-none flex-col gap-px px-sm py-0">
        <NavRow
          icon={SECTION_GLYPHS.connectors}
          current={standing(route, "section:connectors")}
          collapsed={collapsed}
          label={CONNECTORS.label}
          onClick={() => placeSection("connectors", {}, "push")}
        />
        <NavRow
          icon={<WorkspaceGlyph />}
          current={standing(route, "workspace")}
          collapsed={collapsed}
          label="Workspace"
          onClick={() => placeWorkspace(tabs[0], {}, "push")}
        />
      </ul>
      <footer
        className={cn(
          "flex shrink-0 items-center gap-sm px-lg",
          collapsed && "flex-col justify-center px-sm",
        )}
      >
        <SidebarTooltip collapsed={collapsed} label={member.email}>
          <Avatar>
            <AvatarFallback>{member.email.slice(0, 1).toUpperCase()}</AvatarFallback>
          </Avatar>
        </SidebarTooltip>
        <span className={cn("flex min-w-0 flex-1 flex-col", collapsed && "hidden")}>
          <span className="truncate text-label">{member.email}</span>
          <span className="text-small text-ink-soft">{member.admin ? "Admin" : "Member"}</span>
        </span>
        <ChannelsPick collapsed={collapsed} agent={mainAgent} member={member} />
        <SchemePick collapsed={collapsed} />
        <SidebarTooltip collapsed={collapsed} label="Sign out">
          <button
            type="button"
            aria-label="Sign out"
            onClick={signOut}
            className="rounded-control border-0 bg-transparent p-2xs text-ink-soft hover:bg-fill"
          >
            <IconLogout className={GLYPH} aria-hidden />
          </button>
        </SidebarTooltip>
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

function RoutedPane({
  route,
  agents,
  member,
  mainAgent,
  onAgents,
  onExitBuilder,
  onForwardAgents,
  buildWanted,
}: {
  route: Route;
  agents: Agent[];
  member: Member;
  mainAgent: Agent | null;
  onAgents: () => void;
  onExitBuilder: () => void;
  onForwardAgents: () => void;
  buildWanted: boolean;
}) {
  const rail = useRail();
  const tabs = useOfferedTabs();
  /** Where the member came from, off the trail that makes the tab title: every band on this screen
   *  names the trail's innermost step, so they all draw this one step over it and none of them
   *  derives it a second time. */
  const crumb = pageCrumb(route, agents, rail.rows, rail.linked, mainAgent);
  switch (route.kind) {
    case "agent-setup": {
      const app = agents.find((entry) => entry.id === route.agentId) ?? null;
      if (!app) return <PaneNote>No such app.</PaneNote>;
      return (
        <Pane>
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            <Header crumb={crumb} title={SETUP} pinned />
            <div className={cn(COLUMN, "flex-1 overflow-y-auto p-2xl")}>
              <AgentSetup agent={app} onBuilt={onAgents} />
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
          tabs={tabs}
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
    case "agents":
    case "agent": {
      const selected =
        route.kind === "agent"
          ? (agents.find((entry) => entry.id === route.agentId) ?? null)
          : null;
      if (route.kind === "agent" && !selected) return <PaneNote>No such app.</PaneNote>;
      const shown = selected ?? mainAgent;
      return (
        <Pane
          opens={route.kind === "agent" ? (route.place.opens ?? []) : []}
          onMove={(opens) =>
            route.kind === "agent"
              ? placeAgent({ ...route.place, opens }, "replace")
              : shown
                ? openAgentPlace(shown.id, { opens })
                : undefined
          }
        >
          <Agents
            member={member}
            selected={selected}
            build={route.kind === "agents" && route.build === true}
            chats={rail.phase === "ready" ? rail.rows : null}
            onCreated={founded}
            place={route.kind === "agent" ? route.place : {}}
            /* The bare apps hash shows the main agent without having navigated to it, so a
               place set from that screen has no agent in the address to hang on: it names the
               agent it is about and lands on that agent's own address. Answering nothing would
               leave the pane unable to open anything on the one screen the flyout's own exit
               opens. */
            onPlace={(place, step) =>
              route.kind === "agent"
                ? placeAgent(place, step)
                : shown
                  ? openAgentPlace(shown.id, place)
                  : undefined
            }
            onAgents={onAgents}
            onExitBuilder={onExitBuilder}
            onForwardAgents={onForwardAgents}
            buildWanted={buildWanted}
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
              <Loading />
            </PaneNote>
          );
        if (rail.phase === "failed") return <PaneNote>Couldn't load conversations.</PaneNote>;
        const outcome = rail.sought[route.conversationId];
        if (!outcome)
          return (
            <PaneNote>
              <Loading />
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

/** The chats header and the settings menu it holds: the same band and the same menu the apps
 *  header carries, so the two sections read and act as one system. A sort is a pick
 *  between ladders and a surface is a choice turned on and off, which is why one shuts the menu and
 *  the other leaves it standing — the surfaces are read as a set, and the rail adjusts behind the
 *  menu while the member reads what each tick did. */
function RailSettingsFlyout({ shut, onShut }: { shut: boolean; onShut: (shut: boolean) => void }) {
  const { sort, shown } = useRail();
  const host = useDrawerHost();
  return (
    <SectionHead label={CHATS} shut={shut} onShut={onShut}>
      <DropdownMenuContent side="right" align="start" container={host}>
        <DropdownMenuSub>
          <DropdownMenuSubTrigger>Sort by</DropdownMenuSubTrigger>
          <DropdownMenuSubContent>
            <DropdownMenuRadioGroup
              value={sort}
              onValueChange={(value) => pickRailSort(value === "agent" ? "agent" : "recency")}
            >
              <DropdownMenuRadioItem value="recency">Recency</DropdownMenuRadioItem>
              <DropdownMenuRadioItem value="agent">App</DropdownMenuRadioItem>
            </DropdownMenuRadioGroup>
          </DropdownMenuSubContent>
        </DropdownMenuSub>
        <DropdownMenuLabel>Show</DropdownMenuLabel>
        {RAIL_SHOWN_OPTIONS.map((option) => (
          <DropdownMenuCheckboxItem
            key={option.surface}
            checked={shown[option.surface]}
            onCheckedChange={(next) => pickRailShown({ ...shown, [option.surface]: next })}
          >
            {option.label}
          </DropdownMenuCheckboxItem>
        ))}
      </DropdownMenuContent>
    </SectionHead>
  );
}

export const NARROW = "(width < 720px)";

/** Whether the shell is drawing its phone layout, where the hamburger stands on the bar and the
 *  drawer it opens holds the selected section's own list. */
function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(() => window.matchMedia(NARROW).matches);
  useEffect(() => {
    const query = window.matchMedia(NARROW);
    const answer = () => setNarrow(query.matches);
    query.addEventListener("change", answer);
    return () => query.removeEventListener("change", answer);
  }, []);
  return narrow;
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

/** Where a rail row lands. A chat that directs an app — one whose agent is an app's own — reopens
 *  as that app's right-side chat, beside the page it edits. Every other conversation is a regular
 *  chat and loads in the chat app's single column, the way a new conversation does; the shell's own
 *  chat screen stands in where no chat app ships. */
function openRailRow(
  agents: Agent[],
  chatApp: Agent | null,
  conversationId: string,
  agentId: string,
): void {
  const owner = agents.find((agent) => agent.id === agentId);
  const pane = owner?.app ? owner : chatApp;
  if (!pane) {
    openChat(conversationId);
    return;
  }
  openAgentPlace(pane.id, { opens: [conversationId] });
}

function RailList({
  route,
  agents,
  mainAgent,
  chatApp,
}: {
  route: Route;
  agents: Agent[];
  mainAgent: Agent | null;
  chatApp: Agent | null;
}) {
  const rail = useRail();
  const groups = railGroups(rail.rows, rail.sort, rail.shown);
  const folded = railShut(
    rail.shut,
    groups.map((group) => group.label),
  );
  /** The rows a group holds, drawn the same whether a heading stands over them or not. */
  const rows = (group: RailGroup) => (
    <ul className="m-0 flex list-none flex-col gap-px p-0">
      {group.rows.map((row) => {
        const facts = [
          row.speaker ? speakerName(row.speaker) : null,
          isPortalChat(row.surface) ? null : origin(row),
          mainAgent && row.agent_id !== mainAgent.id ? agentName(row.agent_name) : null,
        ].filter((fact): fact is string => fact !== null);
        return (
          <RailRow
            key={row.conversation_id}
            current={standing(route, `open:${row.conversation_id}`)}
            facts={facts.length ? facts.join(" · ") : null}
            title={row.title}
            surface={row.surface}
            onClick={() => openRailRow(agents, chatApp, row.conversation_id, row.agent_id)}
          />
        );
      })}
    </ul>
  );
  return (
    <>
      {rail.phase === "loading" ? (
        <div className="p-sm text-ink-soft">
          <Loading />
        </div>
      ) : null}
      {rail.phase === "failed" ? (
        <div className="flex flex-col gap-2xs p-sm text-ink-soft">
          <span>Couldn't load conversations.</span>
          <button
            type="button"
            onClick={readRail}
            className="w-fit border-0 bg-transparent px-0 py-2xs text-left text-inherit underline"
          >
            Retry
          </button>
        </div>
      ) : null}
      {groups.map((group) => {
        const label = group.label;
        if (label === null) return <Fragment key="rows">{rows(group)}</Fragment>;
        return (
          <Collapsible
            key={label}
            asChild
            open={!folded.includes(label)}
            onOpenChange={(open) =>
              pickRailShut(open ? folded.filter((shut) => shut !== label) : [...folded, label])
            }
          >
            <section>
              <h2 className="m-0">
                <CollapsibleTrigger className="group/rail flex h-(--size-row) w-full items-center gap-2xs rounded-control border-0 bg-transparent px-sm text-left font-sans text-label font-medium text-ink-soft hover:bg-fill">
                  <span className="min-w-0 truncate">{label}</span>
                  <IconChevronRight
                    aria-hidden
                    className="size-icon shrink-0 transition-transform group-data-[state=open]/rail:rotate-90"
                  />
                </CollapsibleTrigger>
              </h2>
              <CollapsibleContent asChild>{rows(group)}</CollapsibleContent>
            </section>
          </Collapsible>
        );
      })}
    </>
  );
}

/** A fact the group above cannot state — the agent holding the conversation, the surface it came
 *  in on, whoever else spoke — is read on the way to a decision, not scanned. As a second line it
 *  doubles every row in the rail to serve the few that carry one, so it is held at the pointer and
 *  the rail keeps one pitch. A row with no such fact triggers nothing and draws no tooltip. The
 *  glyph is the exception: it costs the row no height, so the surface is scanned as well as read.
 *
 *  The title itself is the other half of that bargain. The rail is one column wide and a
 *  conversation is named in a sentence, so the row states as much of the title as it holds and
 *  ellipses the rest — until the member puts the pointer or the keyboard on it, when the title
 *  travels far enough left to state its tail and stays there until they leave. The travel is
 *  measured at that moment rather than held: a title that fits moves nothing, and one measured
 *  before the face it is set in had loaded would travel the wrong distance. It is the words' own
 *  width that is measured, not the frame's overflow, which reports the ellipsis rather than the
 *  text behind it.
 *
 *  The resting title is an inline run so that the frame ellipses it, the way every other truncated
 *  row in the sidebar is drawn; the moment it travels it becomes a box, because a transform does
 *  not move an inline one. The ellipsis goes with it — a mark that says "there is more" has
 *  nothing to say while the more is being read, and left standing it sits over the moving words. */
function RailRow({
  current,
  facts,
  title,
  surface,
  onClick,
}: {
  current: boolean;
  facts: string | null;
  title: string;
  surface: string;
  onClick: () => void;
}) {
  const [asks, setAsks] = useState(0);
  const button = (
    <SidebarPress
      current={current}
      label={title}
      className="gap-xs"
      onClick={onClick}
      onPointerEnter={() => setAsks((asked) => asked + 1)}
      onPointerLeave={() => setAsks(0)}
      onFocus={() => setAsks((asked) => asked + 1)}
      onBlur={() => setAsks(0)}
    >
      <Ticker asks={asks} className="flex-1">
        {title}
      </Ticker>
      <SurfaceGlyph surface={surface} />
    </SidebarPress>
  );
  return (
    <SidebarRow current={current}>
      {facts ? (
        <Tooltip>
          <TooltipTrigger asChild>{button}</TooltipTrigger>
          <TooltipContent>{facts}</TooltipContent>
        </Tooltip>
      ) : (
        button
      )}
    </SidebarRow>
  );
}

const SURFACE_GLYPH = "size-(--size-glyph) shrink-0 text-ink-faint";

/** The surface a conversation came in on, drawn at the far end of its row. The portal draws none:
 *  the rail is read in the portal, so a glyph on every row would state where the member already is.
 *  The words for the same fact stay in the row's tooltip, which is what a reader unable to see the
 *  glyph gets.
 *
 *  Since most rows carry no glyph, one drawn ahead of the title would indent that row alone and
 *  leave the rail without a left edge to read down. It trails instead, where it marks the few rows
 *  that have it without moving the many that do not, and it is drawn faint: a title is what the
 *  member scans for, and the surface is the answer to a question they have already asked.
 *
 *  It is drawn at `--size-glyph`, the size every other mark in the sidebar takes, rather than at the
 *  row's own text size: a glyph scaled to a 13px label is read as a smudge beside a title that runs
 *  the width of the rail, and a mark nobody can name states no surface. */
function SurfaceGlyph({ surface }: { surface: string }) {
  if (surface === SLACK_SURFACE) return <IconBrandSlack className={SURFACE_GLYPH} aria-hidden />;
  if (surface === UFO_SURFACE) return <IconTerminal2 className={SURFACE_GLYPH} aria-hidden />;
  if (surface === IMESSAGE_SURFACE) {
    return <IconMessageCircle className={SURFACE_GLYPH} aria-hidden />;
  }
  return null;
}

const SURFACE_MARK = "size-(--size-surface-mark) shrink-0";

function surfaceMark(surface: string): React.ReactNode {
  if (surface === SLACK_SURFACE) return <IconBrandSlack className={SURFACE_MARK} aria-hidden />;
  if (surface === UFO_SURFACE) return <IconTerminal2 className={SURFACE_MARK} aria-hidden />;
  if (surface === IMESSAGE_SURFACE) {
    return <IconMessageCircle className={SURFACE_MARK} aria-hidden />;
  }
  return null;
}

/** The surface a conversation is happening on, at the head of the pane that reads it: the mark drawn
 *  larger than a rail row's glyph, and the room the surface named beside it. Where that surface
 *  reported where the conversation opened, the pair is the way out to it — drawn the way every act
 *  that leaves the portal is, keeping the line's own colour with no resting underline and the arrow
 *  muted beside the words. Where it reported none, the same pair states the fact and goes nowhere:
 *  a terminal session is not a place a link can land.
 *
 *  A surface with no mark of its own draws nothing at all. The header already names the agent, and
 *  a conversation read here is read-only whatever holds it, which the line under the transcript
 *  says in words. */
function SurfaceMark({ conversation }: { conversation: OwnedConversation }) {
  const mark = surfaceMark(conversation.surface);
  if (mark === null) return null;
  const where = origin(conversation);
  const href = slackLink(conversation.surface, conversation.source);
  if (href === null) {
    return (
      <span className="flex items-center gap-xs whitespace-nowrap text-ink-soft">
        {mark}
        {where}
      </span>
    );
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={"Open " + where + " in " + surfaceWord(conversation.surface)}
      className="flex items-center gap-xs whitespace-nowrap text-inherit no-underline hover:underline focus-visible:underline"
    >
      {mark}
      {where} <span className="text-ink-soft">↗</span>
    </a>
  );
}
