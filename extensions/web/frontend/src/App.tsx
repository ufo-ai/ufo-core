import { useCallback, useEffect, useState } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import {
  IconAdjustments,
  IconApps,
  IconBrandSlack,
  IconChevronRight,
  IconDeviceDesktop,
  IconEdit,
  IconLayoutGrid,
  IconLayoutSidebarRight,
  IconLogout,
  IconMenu2,
  IconMessageCircle,
  IconMoon,
  IconPlug,
  IconSettings,
  IconSun,
  IconTerminal2,
  IconUsers,
  IconX,
} from "@tabler/icons-react";

import logo from "@/assets/ufo-logo.svg";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { SILENT, Toast } from "@/components/ui/toast";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { Admin } from "@/views/Admin";
import { AgentSetup } from "@/views/AgentSetup";

import { Agents, AppsIndex } from "@/views/Agents";
import { ArchivedAppsProvider } from "@/views/ArchivedApps";
import { Chat } from "@/views/Chat";
import { ChatPane, ConversationSlot } from "@/views/ChatPane";
import { ConversationSlotPane } from "@/views/ConversationSlotPane";
import { ConversationDetail, Disclose, subject } from "@/views/Conversations";
import { FirstRun } from "@/views/FirstRun";
import { SignIn } from "@/views/SignIn";
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
  origin,
  slackLink,
  speakerName,
  surfaceWord,
  useViewer,
} from "@/lib/audience";
import { SIGN_OUT_PATH } from "@/lib/api";
import { DrawerHost, useDrawerHost, useDrawerList, useDrawerSlot } from "@/kernel/drawer";
import { COLUMN, Header, Pane, PaneNote } from "@/kernel/pane";
import { Waiting } from "@/kernel/panel";
import { AgentIcon } from "@/lib/agentIcon";
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
import { RAIL_SHOWN_OPTIONS, railGroups, railShut, stampIso } from "@/lib/rail";
import {
  foldSidebar,
  pickPinned,
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
  openAdmin,
  openAgent,
  openAgentPlace,
  openAgents,
  openBuilder,
  openChat,
  openHome,
  openNewChat,
  openSlot,
  placeAgent,
  placeSection,
  placeWorkspace,
  startRouter,
  useRoute,
} from "@/lib/router";
import {
  COMPOSE,
  COMPOSING,
  WORKSPACE_TABS,
  standing,
  type Route,
  type Section,
  type WorkspacePlace,
} from "@/lib/route";
import type { Agent, ArchivedApp, Member, OwnedConversation } from "@/lib/types";

export type AppProps = {
  agents: Agent[];
  archived?: ArchivedApp[];
  member: Member;
  onAgents: () => void;
};

export function App({ agents, archived = [], member, onAgents }: AppProps) {
  const route = useRoute();
  const rail = useRail();
  const [menu, setMenu] = useState(false);
  const shutMenu = useCallback(() => setMenu(false), []);
  const narrow = useNarrow();
  useScrollMark();
  const mainAgent = agents.find((agent) => agent.main) ?? agents[0] ?? null;

  /** The router owns the address: it states the boot address in the bar, lands the arrival on the
   *  track the screen was left holding, and follows the browser from there. It starts here rather
   *  than while the shell renders, because it writes the address and the track store both. */
  useEffect(startRouter, []);

  useEffect(readRail, []);

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
      <Viewer.Provider value={member.email}>
        <MainAgentProvider agents={agents}>
          {mainAgent ? (
            <FirstRun
              agent={mainAgent}
              member={member}
              onOpenChat={() => openNewChat(mainAgent.id)}
            />
          ) : (
            <PaneNote>No such app.</PaneNote>
          )}
        </MainAgentProvider>
      </Viewer.Provider>
    );
  }

  return (
    <Viewer.Provider value={member.email}>
      <MainAgentProvider agents={agents}>
        <TooltipProvider>
          <DrawerHost hosted={narrow} shut={shutMenu}>
            <div
              className={cn(
                "grid h-dvh max-narrow:grid-cols-1 max-narrow:grid-rows-[auto_1fr]",
                rail.collapsed
                  ? "grid-cols-[var(--container-rail)_1fr]"
                  : "grid-cols-[var(--container-sidebar)_1fr]",
              )}
            >
              {narrow ? (
                <NarrowBar agents={agents} member={member} menu={menu} onMenu={setMenu} />
              ) : null}
              <WorkspaceSidebar
                route={route}
                agents={agents}
                member={member}
                mainAgent={mainAgent}
                narrow={narrow}
                onBuild={startBuild}
              />
              <ArchivedAppsProvider apps={archived} onRestored={onAgents}>
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
              </ArchivedAppsProvider>
              <Toast state={rail.fault ?? SILENT} onDone={quietRail} />
            </div>
          </DrawerHost>
        </TooltipProvider>
      </MainAgentProvider>
    </Viewer.Provider>
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
        {member.admin ? (
          <DropdownMenuItem onSelect={openAdmin}>Administration</DropdownMenuItem>
        ) : null}
        <DropdownMenuItem onSelect={signOut}>Sign out</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

const NAV_ROW =
  "flex h-(--size-row) w-full items-center gap-md rounded-full border-0 bg-transparent px-sm text-left text-label text-inherit hover:bg-fill";

/** The rows the sidebar pins where the member has pinned none themselves: the workspace's shipped
 *  apps, in name order, so the sidebar arrives holding its own destinations. The chat app is not one
 *  of them — the New conversation row above is the way to it. */
function defaultPins(agents: Agent[]): string[] {
  const apps = agents.filter((agent) => agent.app && agent.app !== CHAT_SURFACE);
  apps.sort((a, b) => a.name.localeCompare(b.name));
  return apps.map((agent) => agent.id);
}

const GLYPH = "size-(--size-glyph) shrink-0";

const NewChatGlyph = () => <IconEdit className={GLYPH} aria-hidden />;
const AppsGlyph = () => <IconApps className={GLYPH} aria-hidden />;
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

function SidebarToggle({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onClick}
      className="rounded-control border-0 bg-transparent p-xs text-ink-soft hover:bg-fill"
    >
      <IconLayoutSidebarRight className={GLYPH} aria-hidden />
    </button>
  );
}

function NavRow({
  icon,
  current,
  collapsed,
  label,
  className,
  onClick,
  children,
}: {
  icon: React.ReactNode;
  current: boolean;
  collapsed: boolean;
  label: string;
  className?: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  const button = (
    <button
      type="button"
      aria-label={collapsed ? label : undefined}
      aria-current={current}
      onClick={onClick}
      className={cn(
        NAV_ROW,
        collapsed && "justify-center gap-0 px-0",
        current && "bg-fill",
        className,
      )}
    >
      {icon}
      <span className={cn("min-w-0 flex-1 truncate", collapsed && "hidden")}>{children}</span>
    </button>
  );
  return (
    <SidebarTooltip collapsed={collapsed} label={label}>
      {button}
    </SidebarTooltip>
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

/** The Applications row and the flyout it holds. The row is the way to the apps screen; resting on
 *  it — or opening its chevron, which is what a touch screen has — raises the apps index whole:
 *  every app with its live status, the run in flight, and the New application act, so switching
 *  apps is one hover from anywhere. */
function ApplicationsFlyout({
  route,
  agents,
  collapsed,
  pinned,
  onPin,
  onBuild,
}: {
  route: Route;
  agents: Agent[];
  collapsed: boolean;
  pinned: string[];
  onPin: (agentId: string) => void;
  onBuild: () => void;
}) {
  const [open, setOpen] = useState(false);
  const host = useDrawerHost();
  const building = route.kind === "agents" && route.build === true;
  const openId = route.kind === "agent" ? route.agentId : null;
  return (
    <DropdownMenu open={open} onOpenChange={setOpen} modal={false}>
      {/* The section's header, not a destination: the same muted band Conversations wears, and
          the same disclosure act a rail group's heading carries — the whole line the trigger for
          the flyout that holds everything the section reaches. */}
      {collapsed ? (
        <SidebarTooltip collapsed={collapsed} label="Applications">
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              aria-label="Applications"
              className={cn(NAV_ROW, "justify-center gap-0 px-0")}
            >
              <AppsGlyph />
            </button>
          </DropdownMenuTrigger>
        </SidebarTooltip>
      ) : (
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className={cn(
              "flex h-(--size-row) w-full items-center gap-sm rounded-control border-0",
              "bg-transparent px-sm text-left font-sans text-label font-medium text-ink-soft",
              "hover:bg-fill data-[state=open]:bg-fill",
            )}
          >
            <span className="min-w-0 flex-1 truncate">Applications</span>
            <IconLayoutGrid className="size-(--size-glyph) shrink-0" aria-hidden />
          </button>
        </DropdownMenuTrigger>
      )}
      {/* The index is a list of its own rows rather than menu items, so a pick shuts the menu from
          here — nothing inside it is an item Radix would shut it for. */}
      <DropdownMenuContent
        side="right"
        align="start"
        container={host}
        className="max-h-96 w-sidebar p-0 py-sm"
      >
        <AppsIndex
          agents={agents}
          openId={openId}
          building={building}
          pinned={pinned}
          onPin={onPin}
          onOpen={(agentId) => {
            setOpen(false);
            openAgent(agentId);
          }}
          onBuild={() => {
            setOpen(false);
            onBuild();
          }}
        />
      </DropdownMenuContent>
    </DropdownMenu>
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
  /* A drawer is always drawn whole, so the fold a desk width holds is ignored while it stands. */
  const collapsed = rail.collapsed && !narrow;
  const pinned = rail.pinned ?? defaultPins(agents);
  const chatApp = chatSurface(agents);
  return useDrawerList(
    <nav
      aria-label="Workspace"
      className={cn(
        "flex min-h-0 flex-col gap-sm border-r border-edge bg-sidebar py-2xl",
        "max-narrow:flex-1 max-narrow:border-r-0 max-narrow:py-0",
      )}
    >
      <div
        className={cn(
          "flex h-(--size-row) shrink-0 items-center max-narrow:hidden",
          collapsed ? "justify-center px-sm" : "justify-between pl-2xl pr-md",
        )}
      >
        <span
          role="img"
          aria-label="ufo"
          className={cn("h-(--size-wordmark) w-(--size-logo) bg-current", collapsed && "hidden")}
          style={{ mask: `url(${logo}) center / contain no-repeat` }}
        />
        <SidebarTooltip collapsed={collapsed} label="Expand sidebar">
          <SidebarToggle
            label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            onClick={() => foldSidebar(!collapsed)}
          />
        </SidebarTooltip>
      </div>
      <ul className="m-0 flex list-none flex-col gap-px px-sm py-0">
        {narrow ? null : (
          <li>
            <Spotlight
              agents={agents}
              className={cn(NAV_ROW, collapsed && "justify-center gap-0 px-0")}
              label={
                <span className={cn("min-w-0 flex-1 truncate", collapsed && "hidden")}>
                  Search
                </span>
              }
            />
          </li>
        )}
        {mainAgent ? (
          <li>
            <NavRow
              icon={<NewChatGlyph />}
              current={standing(route, COMPOSING)}
              collapsed={collapsed}
              label="New conversation"
              onClick={() =>
                chatApp
                  ? openAgentPlace(chatApp.id, { opens: [COMPOSE] })
                  : openNewChat(mainAgent.id)
              }
            >
              New conversation
            </NavRow>
          </li>
        ) : null}
      </ul>
      <div className="flex shrink-0 flex-col gap-px px-sm">
        <ApplicationsFlyout
          route={route}
          agents={agents}
          collapsed={collapsed}
          pinned={pinned}
          onPin={(agentId) =>
            pickPinned(
              pinned.includes(agentId)
                ? pinned.filter((id) => id !== agentId)
                : [...pinned, agentId],
            )
          }
          onBuild={onBuild}
        />
        <ul className="m-0 flex list-none flex-col gap-px p-0">
          {pinned.map((id) => {
            const agent = agents.find((entry) => entry.id === id);
            if (!agent) return null;
            return (
              <li key={agent.id}>
                <NavRow
                  icon={<AgentIcon name={agent.icon} className="size-(--size-glyph) shrink-0" />}
                  current={standing(route, `agent:${agent.id}`)}
                  collapsed={collapsed}
                  label={agentName(agent.name)}
                  onClick={() => openAgent(agent.id)}
                >
                  {agentName(agent.name)}
                </NavRow>
              </li>
            );
          })}
        </ul>
      </div>
      <div className={cn("shrink-0 px-sm", collapsed && "hidden")}>
        <RailSettingsFlyout />
      </div>
      <div
        className={cn(
          "flex min-h-0 flex-1 flex-col gap-sm overflow-y-auto px-sm",
          collapsed && "hidden",
        )}
      >
        <RailList route={route} agents={agents} mainAgent={mainAgent} chatApp={chatApp} />
      </div>
      <ul className="m-0 mt-auto flex shrink-0 list-none flex-col gap-px px-sm py-0">
        <li>
          <NavRow
            icon={SECTION_GLYPHS.connectors}
            current={standing(route, "section:connectors")}
            collapsed={collapsed}
            label={CONNECTORS.label}
            onClick={() => placeSection("connectors", {}, "push")}
          >
            {CONNECTORS.label}
          </NavRow>
        </li>
        <li>
          <NavRow
            icon={<WorkspaceGlyph />}
            current={standing(route, "workspace")}
            collapsed={collapsed}
            label="Workspace"
            onClick={() => placeWorkspace("team", {}, "push")}
          >
            Workspace
          </NavRow>
        </li>
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
        <SchemePick collapsed={collapsed} />
        {member.admin ? (
          <SidebarTooltip collapsed={collapsed} label="Administration">
            <button
              type="button"
              aria-label="Administration"
              onClick={openAdmin}
              className="rounded-control border-0 bg-transparent p-2xs text-ink-soft hover:bg-fill"
            >
              <IconSettings className={GLYPH} aria-hidden />
            </button>
          </SidebarTooltip>
        ) : null}
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
  /** Where the member came from, off the trail that makes the tab title: every band on this screen
   *  names the trail's innermost step, so they all draw this one step over it and none of them
   *  derives it a second time. */
  const crumb = pageCrumb(route, agents, rail.rows, rail.linked, mainAgent);
  switch (route.kind) {
    case "admin":
      return <Admin />;
    case "agent-setup": {
      const app = agents.find((entry) => entry.id === route.agentId) ?? null;
      if (!app) return <PaneNote>No such app.</PaneNote>;
      return (
        <Pane className={COLUMN}>
          <Header crumb={crumb} title={SETUP} lede={app.purpose ?? undefined} />
          <div className="flex-1 overflow-y-auto p-2xl">
            <AgentSetup agent={app} admin={member.admin} />
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
          tabs={WORKSPACE_TABS}
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

/** The conversations header and the settings menu it holds: the same band and the same menu the
 *  Applications header carries, so the two sections read and act as one system. A sort is a pick
 *  between ladders and a surface is a choice turned on and off, which is why one shuts the menu and
 *  the other leaves it standing — the surfaces are read as a set, and the rail adjusts behind the
 *  menu while the member reads what each tick did. */
function RailSettingsFlyout() {
  const { sort, shown } = useRail();
  const host = useDrawerHost();
  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className={cn(
            "flex h-(--size-row) w-full items-center gap-sm rounded-control border-0",
            "bg-transparent px-sm text-left font-sans text-label font-medium text-ink-soft",
            "hover:bg-fill data-[state=open]:bg-fill",
          )}
        >
          <span className="min-w-0 flex-1 truncate">Conversations</span>
          <IconAdjustments className="size-(--size-glyph) shrink-0" aria-hidden />
        </button>
      </DropdownMenuTrigger>
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
    </DropdownMenu>
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
  const now = new Date();
  const groups = railGroups(rail.rows, rail.sort, rail.shown, now);
  const folded = railShut(
    rail.shut,
    groups.map((group) => group.label),
  );
  return (
    <>
      {rail.phase === "loading" ? (
        <div className="p-sm text-ink-soft">
          <Waiting />
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
      {groups.map((group) => (
        <Collapsible
          key={group.label}
          asChild
          open={!folded.includes(group.label)}
          onOpenChange={(open) =>
            pickRailShut(
              open ? folded.filter((label) => label !== group.label) : [...folded, group.label],
            )
          }
        >
          <section>
            <h2 className="m-0">
              <CollapsibleTrigger className="group/rail flex h-(--size-row) w-full items-center gap-2xs rounded-control border-0 bg-transparent px-sm text-left font-sans text-label font-medium text-ink-soft hover:bg-fill">
                <span className="min-w-0 truncate">{group.label}</span>
                <IconChevronRight
                  aria-hidden
                  className="size-icon shrink-0 transition-transform group-data-[state=open]/rail:rotate-90"
                />
              </CollapsibleTrigger>
            </h2>
            <CollapsibleContent asChild>
              <ul className="m-0 flex list-none flex-col gap-px p-0">
                {group.rows.map((row) => {
                  const facts = [
                    row.speaker ? speakerName(row.speaker) : null,
                    isPortalChat(row.surface) ? null : origin(row),
                    mainAgent && row.agent_id !== mainAgent.id ? agentName(row.agent_name) : null,
                  ].filter((fact): fact is string => fact !== null);
                  return (
                    <li key={row.conversation_id}>
                      <RailRow
                        current={standing(route, `open:${row.conversation_id}`)}
                        facts={facts.length ? facts.join(" · ") : null}
                        onClick={() =>
                          openRailRow(agents, chatApp, row.conversation_id, row.agent_id)
                        }
                      >
                        <span className="min-w-0 flex-1 truncate">{row.title}</span>
                        <SurfaceGlyph surface={row.surface} />
                      </RailRow>
                    </li>
                  );
                })}
              </ul>
            </CollapsibleContent>
          </section>
        </Collapsible>
      ))}
    </>
  );
}

/** A fact the group above cannot state — the agent holding the conversation, the surface it came
 *  in on, whoever else spoke — is read on the way to a decision, not scanned. As a second line it
 *  doubles every row in the rail to serve the few that carry one, so it is held at the pointer and
 *  the rail keeps one pitch. A row with no such fact triggers nothing and draws no tooltip. The
 *  glyph is the exception: it costs the row no height, so the surface is scanned as well as read. */
function RailRow({
  current,
  facts,
  onClick,
  children,
}: {
  current: boolean;
  facts: string | null;
  onClick: () => void;
  children: React.ReactNode;
}) {
  const button = (
    <button
      type="button"
      aria-current={current}
      onClick={onClick}
      className={cn(
        "flex h-(--size-row) w-full items-center gap-xs rounded-row border-0 bg-transparent px-sm text-left text-label text-inherit hover:bg-fill",
        current && "bg-fill",
      )}
    >
      {children}
    </button>
  );
  if (!facts) return button;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{button}</TooltipTrigger>
      <TooltipContent>{facts}</TooltipContent>
    </Tooltip>
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
