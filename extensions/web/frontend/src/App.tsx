import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import {
  IconChevronDown,
  IconCirclePlus,
  IconDeviceDesktop,
  IconLogout,
  IconMenu2,
  IconMoon,
  IconMessages,
  IconPlug,
  IconPlus,
  IconSun,
  IconUsers,
  IconX,
} from "@tabler/icons-react";

import logo from "@/assets/ufo-logo.svg";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { SILENT, Toast } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";
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
import { CONNECTORS, MESSAGING, SECTION_VIEWS, WORKSPACE_VIEWS, type PaneView } from "@/views/registry";
import { WEB_SURFACE, isPortalChat, Viewer, WorkspaceId, surfaceWord, useViewer } from "@/lib/audience";
import { SIGN_OUT_PATH } from "@/lib/api";
import { useAppStatus } from "@/lib/appStatusStore";
import { DrawerHost, useDrawerList, useDrawerSlot } from "@/kernel/drawer";
import { COLUMN, Header, Pane, PaneNote } from "@/kernel/pane";
import { Loading } from "@/kernel/panel";
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
import { stampIso, type ChatRow } from "@/lib/rail";
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
  openAgent,
  openAgentPlace,
  openApps,
  openBuilder,
  openChat,
  openHome,
  openHomeWithConnectors,
  openNewChat,
  openSlot,
  placeAgent,
  placeFirstRun,
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
  homeConversationLane,
  homeLaneAgent,
  homeLaneConversation,
  mintHomeLane,
  standing,
  type Route,
  type Section,
  type WorkspacePlace,
} from "@/lib/route";
import { heldTrack } from "@/lib/tracks";
import { ALL_SURFACES, SurfacesProvider, useOfferedTabs } from "@/lib/surfaces";
import type { Agent, ArchivedApp, Member, OwnedConversation, Surfaces } from "@/lib/types";
import { useNarrow } from "@/lib/narrow";

export type AppProps = {
  agents: Agent[];
  archived?: ArchivedApp[];
  member: Member;
  surfaces?: Surfaces;
  onAgents: () => void;
};

function inSetup(route: Route, agents: Agent[]): boolean {
  if (route.kind !== "agent" && route.kind !== "agent-setup") return false;
  return agents.some((agent) => agent.id === route.agentId && agent.stands_on_setup === true);
}

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
  const listed = agents.filter((agent) => !agent.hidden);
  const pinned = rail.pinned ?? defaultPins(listed);

  const homePlace = useMemo<WorkspacePlace>(
    () => (route.kind === "home" ? route.place : {}),
    [route],
  );
  const homeOpens = useMemo(() => homePlace.opens ?? heldTrack("home"), [homePlace]);
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
  const [seeking, setSeeking] = useState<Seek | undefined>(undefined);
  useEffect(() => {
    if (route.kind !== "home") setSeeking(undefined);
  }, [route.kind]);
  const [activeLane, setActiveLane] = useState<string | undefined>(undefined);
  const enterLane = useCallback(
    (lane: string, opens: string[]) => {
      setSeeking({ id: lane, expansion: homeOpens.includes(lane) ? "switch" : "restore" });
      placeHome({ ...homePlace, opens });
    },
    [homeOpens, homePlace],
  );

  useEffect(startRouter, []);

  useEffect(readRail, []);

  useProvisioned(agents, onAgents);

  useEffect(() => setMenu(false), [route]);

  /** A drawer left open while the window grows past the breakpoint would trap focus behind a
   *  hamburger the layout no longer draws. */
  useEffect(() => {
    if (!narrow) setMenu(false);
  }, [narrow]);

  useEffect(() => {
    document.title = pageTitle(route, agents, rail.rows, rail.linked);
  }, [route, agents, rail.rows, rail.linked]);

  useEffect(() => {
    if (route.kind === "first-run" && !mainAgent) openHome();
  }, [mainAgent, route.kind]);

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

  if (route.kind === "first-run") {
    return (
      <WorkspaceId.Provider value={member.workspace_id ?? null}>
        <Viewer.Provider value={member.email}>
          <SurfacesProvider surfaces={surfaces}>
            <MainAgentProvider agents={agents} onAgents={onAgents}>
              {mainAgent ? (
                <FirstRun
                  agent={mainAgent}
                  agents={agents}
                  member={member}
                  step={route.step}
                  onStep={placeFirstRun}
                  onClose={() => openNewChat(mainAgent.id)}
                  onDone={(conversationId) => {
                    const speaks = chatSurface(agents) ?? mainAgent;
                    openHomeWithConnectors(
                      conversationId
                        ? homeConversationLane(conversationId)
                        : mintHomeLane(speaks.id, []),
                    );
                  }}
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

  const shell = !inSetup(route, agents);

  return (
    <WorkspaceId.Provider value={member.workspace_id ?? null}>
      <Viewer.Provider value={member.email}>
        <SurfacesProvider surfaces={surfaces}>
          <MainAgentProvider agents={agents} onAgents={onAgents}>
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
                  <NarrowBar
                    agents={listed}
                    chats={rail.rows}
                    pinned={pinned}
                    member={member}
                    menu={menu}
                    onMenu={setMenu}
                    onEnterLane={enterLane}
                  />
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
                {!narrow ? (
                  <MinimalSidebar
                    lanes={homeLanes}
                    active={activeLane}
                    agents={listed}
                    chats={rail.rows}
                    pinned={pinned}
                    member={member}
                    main={mainAgent}
                    account={<AccountMenu member={member} />}
                    onLane={(lane) => {
                      if (!homeOpens.includes(lane)) return;
                      enterLane(lane, homeOpens);
                    }}
                    onEnterLane={enterLane}
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

function NarrowBar({
  agents,
  chats,
  pinned,
  member,
  menu,
  onMenu,
  onEnterLane,
}: {
  agents: Agent[];
  chats: ChatRow[];
  pinned: string[];
  member: Member;
  menu: boolean;
  onMenu: (open: boolean) => void;
  onEnterLane: (lane: string, opens: string[]) => void;
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
        chats={chats}
        pinned={pinned}
        onEnterLane={onEnterLane}
        className="ml-auto flex h-(--size-row) items-center rounded-full border-0 bg-transparent px-md text-inherit hover:bg-fill"
      />
      <AccountMenu member={member} />
      <NavDrawer open={menu} onClose={() => onMenu(false)} />
    </header>
  );
}

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

function signOut(): void {
  window.location.assign(SIGN_OUT_PATH);
}

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
const NEW_CHAT = "New chat";
const CREATE_APP = "Create app";

const NAV_ROW =
  "flex h-(--size-row) w-full items-center gap-md rounded-full border-0 bg-transparent px-sm text-left text-label text-inherit hover:bg-fill";

const SECTION_HEAD_CHEVRON =
  "size-icon shrink-0 transition-transform duration-100 ease-control motion-reduce:transition-none";

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
  messaging: <IconMessages className={GLYPH} aria-hidden />,
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
  const tabs = useOfferedTabs();
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
            icon={SECTION_GLYPHS.messaging}
            current={standing(route, "section:messaging")}
            onClick={() => placeSection("messaging", {}, "push")}
          >
            {MESSAGING.label}
          </NavRow>
        </li>
        <li>
          <NavRow
            icon={<WorkspaceGlyph />}
            current={standing(route, "workspace")}
            onClick={() => placeWorkspace(tabs[0], {}, "push")}
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

function SectionLanding({ agentId, place }: { agentId: string; place: WorkspacePlace }) {
  useEffect(() => openAgentPlace(agentId, place), [agentId, place]);
  return null;
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
  const tabs = useOfferedTabs();
  const crumb = pageCrumb(route, agents, rail.rows, rail.linked);
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
    case "first-run":
    case "new-chat": {
      const agent =
        route.kind === "new-chat"
          ? agents.find((entry) => entry.id === route.agentId)
          : (mainAgent ?? undefined);
      if (!agent) return <PaneNote>No such app.</PaneNote>;
      // A key carrying the agent would remount the box on every rename — a fresh box holds the draft again
      // but not the member's place in it, and the cursor lands back at the first character.
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

export const SCROLL_QUIET_MS = 600;

/** `scroll` does not bubble but it does capture, so one listener at the document reaches every scroller
 *  the portal draws, including one mounted after this ran. */
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
