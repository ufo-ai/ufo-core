import { useCallback, useEffect, useRef, useState } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import {
  IconAdjustments,
  IconApps,
  IconBooks,
  IconBrandSlack,
  IconCheck,
  IconChevronRight,
  IconDeviceDesktop,
  IconEdit,
  IconLayoutGrid,
  IconLayoutSidebarRight,
  IconMenu2,
  IconMessageCircle,
  IconMoon,
  IconPin,
  IconPinFilled,
  IconPlug,
  IconRadar,
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
import { SILENT, Toast, type ToastState } from "@/components/ui/toast";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { Admin } from "@/views/Admin";
import { Agents, AppsIndex } from "@/views/Agents";
import { ChatPane } from "@/views/ChatPane";
import { ConversationSlotPane } from "@/views/ConversationSlotPane";
import { ConversationDetail, Disclose, subject } from "@/views/Conversations";
import { FirstRun } from "@/views/FirstRun";
import { SignIn } from "@/views/SignIn";
import { Spotlight } from "@/views/Spotlight";
import { TabbedPane } from "@/views/TabbedPane";
import { SECTION_VIEWS, WORKSPACE_VIEWS } from "@/views/registry";
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
import { DrawerHost, useDrawerList, useDrawerSlot } from "@/kernel/drawer";
import { COLUMN, Pane, PaneHeader } from "@/kernel/pane";
import { AgentIcon } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";
import { MainAgentProvider } from "@/lib/mainAgent";
import { getJson } from "@/lib/api";
import { cn } from "@/lib/cn";
import { SCHEME_OPTIONS, heldScheme, holdScheme, type Scheme } from "@/lib/scheme";
import { pageTitle } from "@/lib/title";
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
import {
  RAIL_SHOWN_OPTIONS,
  bumpChat,
  heldRailShown,
  heldRailShut,
  holdRailShown,
  holdRailShut,
  mergeChats,
  railGroups,
  railShut,
  stampIso,
  type ChatRow,
  type ChatsPayload,
  type RailShown,
  type RailSort,
} from "@/lib/rail";
import {
  AGENTS_HASH,
  BUILDER_HASH,
  HOME_HASH,
  WORKSPACE_TABS,
  agentHash,
  artifactTarget,
  bootRoute,
  FIRST_RUN_HASH,
  chatHash,
  newChatHash,
  parseHash,
  sectionHash,
  workspaceHash,
  type PlaceStep,
  type Route,
  type Section,
  type WorkspacePlace,
  type WorkspaceTab,
} from "@/lib/route";
import type { Agent, Member, OwnedConversation } from "@/lib/types";

export type AppProps = {
  agents: Agent[];
  member: Member;
  onAgents: () => void;
};

type Rail = { phase: "loading" | "failed" | "ready"; rows: ChatRow[] };


type Sought =
  | { kind: "answered" }
  | { kind: "signed-out" }
  | { kind: "failed"; message: string };

export function App({ agents, member, onAgents }: AppProps) {
  const [route, setRoute] = useState<Route>(() => bootRoute(location.hash, location.search));
  useEffect(() => {
    const target = artifactTarget(location.search);
    if (target) location.replace(target);
  }, []);
  const [rail, setRail] = useState<Rail>({ phase: "loading", rows: [] });
  const [toast, setToast] = useState<ToastState>(SILENT);
  const [reloads, setReloads] = useState(0);
  const [sought, setSought] = useState<Readonly<Record<string, Sought>>>({});
  const [linked, setLinked] = useState<Record<string, OwnedConversation>>({});
  const [railSort, setRailSort] = useState<RailSort>(() =>
    localStorage.getItem("rail-sort") === "agent" ? "agent" : "recency",
  );
  const setRailSortHeld = useCallback((next: RailSort) => {
    setRailSort(next);
    localStorage.setItem("rail-sort", next);
  }, []);
  const [railShown, setRailShown] = useState<RailShown>(heldRailShown);
  const setRailShownHeld = useCallback((next: RailShown) => {
    setRailShown(next);
    holdRailShown(next);
  }, []);
  const [menu, setMenu] = useState(false);
  const shutMenu = useCallback(() => setMenu(false), []);
  const narrow = useNarrow();
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem("sidebar") === "collapsed");
  const setSidebarCollapsed = useCallback((next: boolean) => {
    setCollapsed(next);
    localStorage.setItem("sidebar", next ? "collapsed" : "expanded");
  }, []);
  const [pinned, setPinned] = useState<string[]>(heldPinned);
  const togglePin = useCallback((agentId: string) => {
    setPinned((current) => {
      const next = current.includes(agentId)
        ? current.filter((id) => id !== agentId)
        : [...current, agentId];
      holdPinned(next);
      return next;
    });
  }, []);

  /** A drawer left open while the window grows past the breakpoint would trap focus behind a
   *  hamburger the layout no longer draws. */
  useEffect(() => {
    if (!narrow) setMenu(false);
  }, [narrow]);

  const [railShut, setRailShut] = useState<string[] | null>(heldRailShut);
  const setRailShutHeld = useCallback((next: string[]) => {
    setRailShut(next);
    holdRailShut(next);
  }, []);
  const mainAgent = agents.find((agent) => agent.main) ?? agents[0] ?? null;
  const routeRef = useRef(route);
  routeRef.current = route;

  useEffect(() => {
    const booted = routeRef.current;
    if (!location.hash && booted.kind === "chat") {
      history.replaceState(null, "", chatHash(booted.conversationId));
    }
    const onHash = () => setRoute(parseHash(location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    document.title = pageTitle(route, agents, rail.rows, linked, mainAgent);
  }, [route, agents, rail.rows, linked, mainAgent]);

  useEffect(() => {
    let live = true;
    setRail((current) => ({ phase: "loading", rows: current.rows }));
    getJson<ChatsPayload>("/api/chats").then((result) => {
      if (!live) return;
      if (!result.ok && result.status !== 401) {
        setToast({ title: "Conversations did not refresh.", description: result.message });
      }
      setRail((current) =>
        result.ok
          ? { phase: "ready", rows: mergeChats(result.payload.chats, current.rows) }
          : { phase: "failed", rows: current.rows },
      );
    });
    return () => {
      live = false;
    };
  }, [reloads]);

  /** Every act that moves the page comes through here, so the drawer standing over that page is
   *  shut here too — whether the member picked a destination, a conversation, or an app. */
  const go = useCallback((hash: string, next: Route) => {
    if (location.hash !== hash) location.hash = hash;
    setRoute(next);
    setMenu(false);
  }, []);

  // The card reaches the first run by query, since a fragment never reaches the server; the address
  // replaces it in the bar so what the member sees is somewhere they can return to. A workspace
  // with no agent to talk to has no first run and falls back to the home screen.
  useEffect(() => {
    if (route.kind !== "first-run") return;
    if (!mainAgent) {
      go(HOME_HASH, { kind: "home" });
      return;
    }
    if (location.hash !== FIRST_RUN_HASH) {
      history.replaceState(null, "", FIRST_RUN_HASH);
    }
  }, [go, mainAgent, route.kind]);

  const openHome = useCallback(() => go(HOME_HASH, { kind: "home" }), [go]);

  const openChat = useCallback(
    (conversationId: string) => go(chatHash(conversationId), { kind: "chat", conversationId }),
    [go],
  );

  const openNewChat = useCallback(
    (agentId: string) => go(newChatHash(agentId), { kind: "new-chat", agentId }),
    [go],
  );

  const openAgents = useCallback(() => go(AGENTS_HASH, { kind: "agents" }), [go]);

  /** The wizard mounts only behind a member's press or a run already in flight: its address alone
   *  must not found a conversation, or Back and reload would send model turns nobody asked for. */
  const [wantedBuild, setWantedBuild] = useState(false);

  const openBuilder = useCallback(() => {
    setWantedBuild(true);
    go(BUILDER_HASH, { kind: "agents", build: true });
  }, [go]);

  const exitBuilder = useCallback(() => {
    setWantedBuild(false);
    openAgents();
  }, [openAgents]);

  /** Where an unbacked wizard address forwards: the same screen, written over the address rather
   *  than stacked on it, so Back does not land on the forwarder again. */
  const replaceAgents = useCallback(() => {
    setWantedBuild(false);
    history.replaceState(null, "", AGENTS_HASH);
    setRoute({ kind: "agents" });
  }, []);

  /** An agent opened at a place rather than at its head — what the bare apps hash, which shows the
   *  main agent without having navigated to it, turns a place change into. */
  const openAgentPlace = useCallback(
    (agentId: string, place: WorkspacePlace) =>
      go(agentHash(agentId, place), { kind: "agent", agentId, place }),
    [go],
  );

  const openAgent = useCallback(
    (agentId: string) => go(agentHash(agentId), { kind: "agent", agentId, place: {} }),
    [go],
  );

  const stepPlace = useCallback(
    (step: PlaceStep, held: boolean, next: () => { route: Route; hash: string } | null) => {
      if (step !== "push" && !held) return;
      if (step === "back") {
        history.back();
        return;
      }
      const target = next();
      if (!target) return;
      if (step === "replace") {
        history.replaceState(null, "", target.hash);
        setRoute(target.route);
        return;
      }
      go(target.hash, target.route);
    },
    [go],
  );

  const placeAgent = useCallback(
    (place: WorkspacePlace, step: PlaceStep) => {
      const seen = routeRef.current;
      stepPlace(step, seen.kind === "agent", () =>
        seen.kind === "agent"
          ? {
              route: { kind: "agent", agentId: seen.agentId, place },
              hash: agentHash(seen.agentId, place),
            }
          : null,
      );
    },
    [stepPlace],
  );

  const openSlot = useCallback(
    (conversationId: string, slot: string | null) =>
      go(chatHash(conversationId, slot ?? undefined), {
        kind: "chat",
        conversationId,
        ...(slot ? { slot } : {}),
      }),
    [go],
  );

  const placeWorkspace = useCallback(
    (view: WorkspaceTab, place: WorkspacePlace, step: PlaceStep) => {
      const seen = routeRef.current;
      stepPlace(step, seen.kind === "workspace" && seen.view === view, () => ({
        route: { kind: "workspace", view, place },
        hash: workspaceHash(view, place),
      }));
    },
    [stepPlace],
  );

  const placeSection = useCallback(
    (section: Section, place: WorkspacePlace, step: PlaceStep) => {
      const seen = routeRef.current;
      stepPlace(step, seen.kind === "section" && seen.section === section, () => ({
        route: { kind: "section", section, place },
        hash: sectionHash(section, place),
      }));
    },
    [stepPlace],
  );

  const openAdmin = useCallback(() => go("#/admin", { kind: "admin" }), [go]);

  const created = useCallback(
    (agent: Agent, conversationId: string, title: string) => {
      const row: ChatRow = {
        conversation_id: conversationId,
        agent_id: agent.id,
        agent_name: agent.name,
        title,
        last_at: stampIso(new Date()),
        surface: WEB_SURFACE,
        surface_label: null,
        mine: true,
        speaker: null,
      };
      setRail((current) => ({ ...current, rows: mergeChats(current.rows, [row]) }));
      const seen = routeRef.current;
      // Every screen that draws an unfounded chat hands the member to the conversation their
      // message founded. A screen left standing on one would redraw an empty composer over what
      // they just sent.
      const started =
        seen.kind === "home" || (seen.kind === "new-chat" && seen.agentId === agent.id);
      if (started) openChat(conversationId);
    },
    [openChat],
  );

  const activity = useCallback((conversationId: string) => {
    setRail((current) => ({
      ...current,
      rows: bumpChat(current.rows, conversationId, new Date()),
    }));
  }, []);

  useEffect(() => {
    if (route.kind !== "chat" || rail.phase !== "ready") return;
    const wanted = route.conversationId;
    if (
      rail.rows.some((row) => row.conversation_id === wanted && isPortalChat(row.surface)) ||
      wanted in sought
    ) {
      return;
    }
    let live = true;
    getJson<ChatsPayload>("/api/chats?conversation=" + wanted).then((result) => {
      if (!live) return;
      const outcome: Sought = result.ok
        ? { kind: "answered" }
        : result.status === 401
          ? { kind: "signed-out" }
          : { kind: "failed", message: result.message };
      setSought((current) => ({ ...current, [wanted]: outcome }));
      if (!result.ok) return;
      if (result.payload.chats.length) {
        setRail((current) => ({ ...current, rows: mergeChats(current.rows, result.payload.chats) }));
      }
      const conversation = result.payload.conversation;
      if (conversation) {
        setLinked((current) => ({ ...current, [wanted]: conversation }));
      }
    });
    return () => {
      live = false;
    };
  }, [route, rail, sought]);

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
                collapsed
                  ? "grid-cols-[var(--container-rail)_1fr]"
                  : "grid-cols-[var(--container-sidebar)_1fr]",
              )}
            >
              {narrow ? (
                <NarrowBar
                  agents={agents}
                  member={member}
                  menu={menu}
                  onMenu={setMenu}
                  onHome={openHome}
                  onAdmin={openAdmin}
                />
              ) : null}
              <WorkspaceSidebar
                route={route}
                agents={agents}
                member={member}
                mainAgent={mainAgent}
                narrow={narrow}
                collapsed={collapsed && !narrow}
                onCollapsed={setSidebarCollapsed}
                rail={rail}
                sort={railSort}
                onSort={setRailSortHeld}
                shown={railShown}
                onShown={setRailShownHeld}
                shut={railShut}
                onShut={setRailShutHeld}
                onNewChat={openNewChat}
                onOpen={openChat}
                onRetry={() => setReloads((count) => count + 1)}
                onOpenAgent={openAgent}
                onBuild={openBuilder}
                onSection={(section) => placeSection(section, {}, "push")}
                onWorkspace={() => placeWorkspace("team", {}, "push")}
                onAdmin={openAdmin}
                pinned={pinned}
                onPin={togglePin}
              />
              <RoutedPane
                route={route}
                agents={agents}
                member={member}
                mainAgent={mainAgent}
                rail={rail}
                onAgents={onAgents}
                onExitBuilder={exitBuilder}
                onForwardAgents={replaceAgents}
                buildWanted={wantedBuild}
                onCreated={created}
                onActivity={activity}
                onOpenAgent={openAgent}
                onOpenAgentPlace={openAgentPlace}
                onNewChat={openNewChat}
                onOpenSlot={openSlot}
                onPlaceWorkspace={placeWorkspace}
                onPlaceSection={placeSection}
                onPlaceAgent={placeAgent}
                sought={sought}
                linked={linked}
              />
              <Toast state={toast} onDone={() => setToast(SILENT)} />
            </div>
          </DrawerHost>
        </TooltipProvider>
      </MainAgentProvider>
    </Viewer.Provider>
  );
}

/** The bar a phone width keeps: the hamburger that opens the drawer holding the sidebar, the mark
 *  centred between it and the search and account closing the row. A desk width draws no bar at all
 *  — the sidebar is the shell. */
function NarrowBar({
  agents,
  member,
  menu,
  onMenu,
  onHome,
  onAdmin,
}: {
  agents: Agent[];
  member: Member;
  menu: boolean;
  onMenu: (open: boolean) => void;
  onHome: () => void;
  onAdmin: () => void;
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
        onClick={onHome}
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
        onOpen={(hash) => (location.hash = hash)}
      />
      <AccountMenu member={member} onAdmin={onAdmin} />
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
                <IconX className="size-icon" aria-hidden />
              </Button>
            </DialogPrimitive.Close>
          </header>
          <div ref={hold} className="flex min-h-0 flex-1 flex-col" />
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

/** The submenu trigger states the palette the member picked, not the one the browser resolved:
 *  `System` is a choice they can read back, and a value that flipped itself at dusk would say they
 *  had picked light. */
function AccountMenu({ member, onAdmin }: { member: Member; onAdmin: () => void }) {
  const [scheme, setScheme] = useState<Scheme>(heldScheme);
  const pick = (next: Scheme) => {
    holdScheme(next);
    setScheme(next);
  };
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
            <DropdownMenuRadioGroup
              value={scheme}
              onValueChange={(value) =>
                pick(value === "light" || value === "dark" ? value : "system")
              }
            >
              {SCHEME_OPTIONS.map((option) => (
                <DropdownMenuRadioItem key={option.scheme} value={option.scheme}>
                  {option.label}
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuSubContent>
        </DropdownMenuSub>
        {member.admin ? (
          <DropdownMenuItem onSelect={onAdmin}>Administration</DropdownMenuItem>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

const NAV_ROW =
  "flex h-(--size-row) w-full items-center gap-md rounded-full border-0 bg-transparent px-sm text-left text-label text-inherit hover:bg-fill";

/** What the member pinned into the sidebar — apps by id, the fixed reads by name — in the order
 *  they pinned them. Until they have touched a pin the fixed reads stand pinned, so the section
 *  arrives holding its own destinations. */
const PINNED_KEY = "pinned-rows";

function heldPinned(): string[] {
  const held = localStorage.getItem(PINNED_KEY);
  return held === null ? [...APPLICATION_SECTIONS] : held.split("\n").filter(Boolean);
}

function holdPinned(pinned: string[]): void {
  localStorage.setItem(PINNED_KEY, pinned.join("\n"));
}

const GLYPH = "size-(--size-glyph) shrink-0";

const NewChatGlyph = () => <IconEdit className={GLYPH} aria-hidden />;
const AppsGlyph = () => <IconApps className={GLYPH} aria-hidden />;
const WorkspaceGlyph = () => <IconUsers className={GLYPH} aria-hidden />;

const SECTION_GLYPHS: Record<Section, React.ReactNode> = {
  radar: <IconRadar className={GLYPH} aria-hidden />,
  artifacts: <IconBooks className={GLYPH} aria-hidden />,
  connectors: <IconPlug className={GLYPH} aria-hidden />,
};

/** The fixed reads the Applications section carries beside the apps: destinations that read
 *  across apps rather than being one. They pin and unpin exactly as an app does. */
const APPLICATION_SECTIONS: Section[] = ["artifacts", "radar"];

const FLYOUT_ID = "applications-flyout";

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
  const [scheme, setScheme] = useState<Scheme>(heldScheme);
  const pick = (next: Scheme) => {
    holdScheme(next);
    setScheme(next);
  };
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
        <DropdownMenuRadioGroup
          value={scheme}
          onValueChange={(value) => pick(value === "light" || value === "dark" ? value : "system")}
        >
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
  onOpenAgent,
  onBuild,
  onSection,
}: {
  route: Route;
  agents: Agent[];
  collapsed: boolean;
  pinned: string[];
  onPin: (agentId: string) => void;
  onOpenAgent: (agentId: string) => void;
  onBuild: () => void;
  onSection: (section: Section) => void;
}) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const dismiss = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", dismiss);
    return () => document.removeEventListener("keydown", dismiss);
  }, [open]);
  const building = route.kind === "agents" && route.build === true;
  const openId = route.kind === "agent" ? route.agentId : null;
  return (
    <div
      className="relative"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      {/* The section's header, not a destination: the same muted band Conversations wears, and
          the same disclosure act a rail group's heading carries — the whole line the trigger for
          the flyout that holds everything the section reaches. */}
      {collapsed ? (
        <SidebarTooltip collapsed={collapsed} label="Applications">
          <button
            type="button"
            aria-label="Applications"
            aria-expanded={open}
            aria-controls={FLYOUT_ID}
            onClick={() => setOpen((held) => !held)}
            className={cn(NAV_ROW, "justify-center gap-0 px-0")}
          >
            <AppsGlyph />
          </button>
        </SidebarTooltip>
      ) : (
        <button
          type="button"
          aria-expanded={open}
          aria-controls={FLYOUT_ID}
          onClick={() => setOpen((held) => !held)}
          className={cn(
            "flex h-(--size-row) w-full items-center gap-sm rounded-control border-0",
            "bg-transparent px-sm text-left font-sans text-label font-medium text-ink-soft",
            "hover:bg-fill",
          )}
        >
          <span className="min-w-0 flex-1 truncate">Applications</span>
          <IconLayoutGrid className="size-(--size-glyph) shrink-0" aria-hidden />
        </button>
      )}
      {open ? (
        <div
          id={FLYOUT_ID}
          className={cn(
            "absolute left-full top-0 z-10 flex max-h-96 w-sidebar flex-col py-sm",
            "rounded-menu border border-edge bg-popover text-popover-foreground animate-raise",
            "max-narrow:left-0 max-narrow:top-full",
          )}
        >
          <AppsIndex
            agents={agents}
            openId={openId}
            building={building}
            pinned={pinned}
            onPin={onPin}
            onOpen={(agentId) => {
              setOpen(false);
              onOpenAgent(agentId);
            }}
            onBuild={() => {
              setOpen(false);
              onBuild();
            }}
          >
            {APPLICATION_SECTIONS.map((section) => {
              const held = pinned.includes(section);
              const current = route.kind === "section" && route.section === section;
              return (
                <li
                  key={section}
                  className={cn(
                    "group/row flex items-center rounded-row hover:bg-fill",
                    current && "bg-fill",
                  )}
                >
                  <button
                    type="button"
                    aria-current={current}
                    onClick={() => {
                      setOpen(false);
                      onSection(section);
                    }}
                    className={cn(
                      "flex min-h-(--size-row) min-w-0 flex-1 items-center gap-sm border-0",
                      "bg-transparent px-sm py-2xs text-left text-label text-inherit",
                    )}
                  >
                    {SECTION_GLYPHS[section]}
                    <span className="min-w-0 flex-1 truncate">{SECTION_VIEWS[section].label}</span>
                  </button>
                  <button
                    type="button"
                    aria-label={(held ? "Unpin " : "Pin ") + SECTION_VIEWS[section].label}
                    aria-pressed={held}
                    onClick={() => onPin(section)}
                    className={cn(
                      "mr-xs shrink-0 rounded-control border-0 bg-transparent p-2xs text-ink-soft hover:bg-fill",
                      held
                        ? undefined
                        : "opacity-0 group-hover/row:opacity-100 focus-visible:opacity-100",
                    )}
                  >
                    {held ? (
                      <IconPinFilled className="size-icon" aria-hidden />
                    ) : (
                      <IconPin className="size-icon" aria-hidden />
                    )}
                  </button>
                </li>
              );
            })}
          </AppsIndex>
        </div>
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
  collapsed,
  onCollapsed,
  rail,
  sort,
  onSort,
  shown,
  onShown,
  shut,
  onShut,
  onNewChat,
  onOpen,
  onRetry,
  onOpenAgent,
  onBuild,
  onSection,
  onWorkspace,
  onAdmin,
  pinned,
  onPin,
}: {
  route: Route;
  agents: Agent[];
  member: Member;
  mainAgent: Agent | null;
  narrow: boolean;
  collapsed: boolean;
  onCollapsed: (next: boolean) => void;
  rail: Rail;
  sort: RailSort;
  onSort: (sort: RailSort) => void;
  shown: RailShown;
  onShown: (shown: RailShown) => void;
  shut: string[] | null;
  onShut: (shut: string[]) => void;
  onNewChat: (agentId: string) => void;
  onOpen: (conversationId: string) => void;
  onRetry: () => void;
  onOpenAgent: (agentId: string) => void;
  onBuild: () => void;
  onSection: (section: Section) => void;
  onWorkspace: () => void;
  onAdmin: () => void;
  pinned: string[];
  onPin: (agentId: string) => void;
}) {
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
            onClick={() => onCollapsed(!collapsed)}
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
              onOpen={(hash) => (location.hash = hash)}
            />
          </li>
        )}
        {mainAgent ? (
          <li>
            <NavRow
              icon={<NewChatGlyph />}
              current={route.kind === "new-chat" || route.kind === "home"}
              collapsed={collapsed}
              label="New conversation"
              onClick={() => onNewChat(mainAgent.id)}
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
          onPin={onPin}
          onOpenAgent={onOpenAgent}
          onBuild={onBuild}
          onSection={onSection}
        />
        <ul className="m-0 flex list-none flex-col gap-px p-0">
          {pinned.map((id) => {
            const section = APPLICATION_SECTIONS.find((name) => name === id);
            if (section) {
              return (
                <li key={section}>
                  <NavRow
                    icon={SECTION_GLYPHS[section]}
                    current={route.kind === "section" && route.section === section}
                    collapsed={collapsed}
                    label={SECTION_VIEWS[section].label}
                    onClick={() => onSection(section)}
                  >
                    {SECTION_VIEWS[section].label}
                  </NavRow>
                </li>
              );
            }
            const agent = agents.find((entry) => entry.id === id);
            if (!agent) return null;
            return (
              <li key={agent.id}>
                <NavRow
                  icon={<AgentIcon name={agent.icon} className="size-(--size-glyph) shrink-0" />}
                  current={route.kind === "agent" && route.agentId === agent.id}
                  collapsed={collapsed}
                  label={agentName(agent.name)}
                  onClick={() => onOpenAgent(agent.id)}
                >
                  {agentName(agent.name)}
                </NavRow>
              </li>
            );
          })}
        </ul>
      </div>
      <div className={cn("shrink-0 px-sm", collapsed && "hidden")}>
        <RailSettingsFlyout sort={sort} onSort={onSort} shown={shown} onShown={onShown} />
      </div>
      <div
        className={cn(
          "flex min-h-0 flex-1 flex-col gap-sm overflow-y-auto px-sm",
          collapsed && "hidden",
        )}
      >
        <RailList
          rail={rail}
          route={route}
          mainAgent={mainAgent}
          sort={sort}
          shown={shown}
          shut={shut}
          onShut={onShut}
          onOpen={onOpen}
          onRetry={onRetry}
        />
      </div>
      <ul className="m-0 mt-auto flex shrink-0 list-none flex-col gap-px px-sm py-0">
        <li>
          <NavRow
            icon={SECTION_GLYPHS.connectors}
            current={route.kind === "section" && route.section === "connectors"}
            collapsed={collapsed}
            label={SECTION_VIEWS.connectors.label}
            onClick={() => onSection("connectors")}
          >
            {SECTION_VIEWS.connectors.label}
          </NavRow>
        </li>
        <li>
          <NavRow
            icon={<WorkspaceGlyph />}
            current={route.kind === "workspace"}
            collapsed={collapsed}
            label="Workspace"
            onClick={onWorkspace}
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
              onClick={onAdmin}
              className="rounded-control border-0 bg-transparent p-2xs text-ink-soft hover:bg-fill"
            >
              <IconSettings className={GLYPH} aria-hidden />
            </button>
          </SidebarTooltip>
        ) : null}
      </footer>
    </nav>,
  );
}

function RoutedPane({
  route,
  agents,
  member,
  mainAgent,
  rail,
  onAgents,
  onExitBuilder,
  onForwardAgents,
  buildWanted,
  onCreated,
  onActivity,
  onOpenAgent,
  onOpenAgentPlace,
  onNewChat,
  onOpenSlot,
  onPlaceWorkspace,
  onPlaceSection,
  onPlaceAgent,
  sought,
  linked,
}: {
  route: Route;
  agents: Agent[];
  member: Member;
  mainAgent: Agent | null;
  rail: Rail;
  onAgents: () => void;
  onExitBuilder: () => void;
  onForwardAgents: () => void;
  buildWanted: boolean;
  onCreated: (agent: Agent, conversationId: string, title: string) => void;
  onActivity: (conversationId: string) => void;
  onOpenAgent: (agentId: string) => void;
  onOpenAgentPlace: (agentId: string, place: WorkspacePlace) => void;
  onNewChat: (agentId: string) => void;
  onOpenSlot: (conversationId: string, slot: string | null) => void;
  onPlaceWorkspace: (view: WorkspaceTab, place: WorkspacePlace, step: PlaceStep) => void;
  onPlaceSection: (section: Section, place: WorkspacePlace, step: PlaceStep) => void;
  onPlaceAgent: (place: WorkspacePlace, step: PlaceStep) => void;
  sought: Readonly<Record<string, Sought>>;
  linked: Readonly<Record<string, OwnedConversation>>;
}) {
  if (route.kind === "admin") return <Admin />;
  if (route.kind === "bad-link") return <PaneNote>This conversation link is not valid.</PaneNote>;
  if (route.kind === "workspace") {
    return (
      <TabbedPane
        title="Workspace"
        group="workspace"
        tabs={WORKSPACE_TABS}
        views={WORKSPACE_VIEWS}
        view={route.view}
        place={route.place}
        onPlace={onPlaceWorkspace}
      />
    );
  }
  if (route.kind === "section") {
    return (
      <TabbedPane
        title={SECTION_VIEWS[route.section].label}
        group="section"
        tabs={[route.section]}
        views={SECTION_VIEWS}
        view={route.section}
        place={route.place}
        onPlace={onPlaceSection}
      />
    );
  }
  if (route.kind === "agents" || route.kind === "agent") {
    const selected =
      route.kind === "agent" ? (agents.find((entry) => entry.id === route.agentId) ?? null) : null;
    if (route.kind === "agent" && !selected) return <PaneNote>No such app.</PaneNote>;
    const shown = selected ?? mainAgent;
    return (
      <Pane>
        <Agents
          member={member}
          selected={selected}
          build={route.kind === "agents" && route.build === true}
          chats={rail.phase === "ready" ? rail.rows : null}
          onCreated={onCreated}
          place={route.kind === "agent" ? route.place : {}}
          /* The bare apps hash shows the main agent without having navigated to it, so a place set
             from that screen has no agent in the address to hang on: it names the agent it is
             about and lands on that agent's own address. Answering nothing would make the pane's
             conversation switcher dead on the one screen the flyout's own exit opens. */
          onPlace={(place, step) =>
            route.kind === "agent"
              ? onPlaceAgent(place, step)
              : shown
                ? onOpenAgentPlace(shown.id, place)
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
  if (route.kind === "conversation-slot") {
    const agent = agents.find((entry) => entry.id === route.agentId);
    if (!agent) return <PaneNote>No such app.</PaneNote>;
    return (
      <ConversationSlotPane
        agent={agent}
        conversationId={route.conversationId}
        slot={route.slot}
        rootConversationId={route.rootConversationId}
        onOpenAgent={onOpenAgent}
      />
    );
  }
  if (route.kind === "chat") {
    const row = rail.rows.find(
      (entry) => entry.conversation_id === route.conversationId && isPortalChat(entry.surface),
    );
    const linkedConversation = linked[route.conversationId];
    if (!row && linkedConversation) {
      const linkedAgent = agents.find((entry) => entry.id === linkedConversation.agent.id);
      if (!linkedAgent) return <PaneNote>No such app.</PaneNote>;
      if (!linkedConversation.readable && !linkedConversation.disclosable) return <NotShared />;
      return (
        <LinkedPane
          key={linkedConversation.id}
          agent={linkedAgent}
          conversation={linkedConversation}
          slot={route.slot}
          onSelectSlot={(slot) => onOpenSlot(route.conversationId, slot)}
          onOpenAgent={onOpenAgent}
        />
      );
    }
    const listedAgent = row ? agents.find((entry) => entry.id === row.agent_id) : undefined;
    const agent =
      listedAgent ??
      (row && row.surface.startsWith("extension:")
        ? { id: row.agent_id, name: row.agent_name, model: row.agent_model ?? "" }
        : undefined);
    if (!row || !agent) {
      if (rail.phase === "loading") return <PaneNote>Loading…</PaneNote>;
      if (rail.phase === "failed") return <PaneNote>Couldn't load conversations.</PaneNote>;
      const outcome = sought[route.conversationId];
      if (!outcome) return <PaneNote>Loading…</PaneNote>;
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
        onActivity={onActivity}
        title={row.title}
        conversationOnly={!listedAgent}
        onOpenAgent={onOpenAgent}
        slot={route.slot}
        onSelectSlot={(slot) => onOpenSlot(route.conversationId, slot)}
      />
    );
  }
  const agent =
    route.kind === "new-chat"
      ? agents.find((entry) => entry.id === route.agentId)
      : (mainAgent ?? undefined);
  if (!agent) return <PaneNote>No such app.</PaneNote>;
  // One start screen, whichever agent it names. The picker in its own composer renames the agent the
  // first message reaches, and a key carrying that agent would remount the box on every pick — a
  // fresh box holds the draft again but not the member's place in it, and the cursor lands back at
  // the first character. The chat state and the draft are keyed by the agent inside it instead, and
  // its composer takes a pending ask on the agent it is renamed to, because no mount comes to read
  // one handed to the agent the route has just named.
  return (
    <ChatPane
      key="new"
      agent={agent}
      member={member}
      conversationId={null}
      onCreated={(conversationId, title) => onCreated(agent, conversationId, title)}
      onActivity={onActivity}
      agents={agents}
      onPickAgent={onNewChat}
    />
  );
}

/** A conversation another surface holds, read in the portal: the thread pane a portal chat wears,
 *  headed the same way — the agent holding it and what it is called — with the
 *  surface it is happening on marked at the far end of that header, as the way out to it. The
 *  header states the name once, so the transcript under it draws no heading of its own. */
function LinkedPane({
  agent,
  conversation,
  slot,
  onSelectSlot,
  onOpenAgent,
}: {
  agent: Agent;
  conversation: OwnedConversation;
  slot?: string;
  onSelectSlot: (slot: string | null) => void;
  onOpenAgent: (agentId: string) => void;
}) {
  const [disclosed, setDisclosed] = useState(false);
  const viewer = useViewer();
  const readable = conversation.readable || disclosed;
  return (
    <Pane>
      <div
        className={cn(
          "relative grid min-h-0 flex-1 grid-cols-1",
          readable && slot && "grid-cols-(--grid-slot) max-narrow:grid-cols-1",
        )}
      >
        <div
          className={cn("flex min-h-0 min-w-0 flex-col", readable && slot && "max-narrow:invisible")}
        >
          <PaneHeader
            parent={{ label: agentName(agent.name), onGo: () => onOpenAgent(agent.id) }}
            current={subject(conversation, viewer)}
            actions={<SurfaceMark conversation={conversation} />}
          />
          <div className={cn(COLUMN, "flex-1 overflow-y-auto p-2xl")} data-testid="panel">
            {readable ? (
              <>
                <ConversationDetail
                  agent={agent}
                  conversation={conversation}
                  headed
                  onOpenArtifacts={() => onSelectSlot("artifacts")}
                />
                <p className="max-w-hint text-ink-soft">
                  This conversation is read-only here. Reply in {surfaceWord(conversation.surface)}{" "}
                  to continue it.
                </p>
              </>
            ) : (
              <Disclose
                agent={agent}
                conversation={conversation}
                onOpened={() => setDisclosed(true)}
              />
            )}
          </div>
        </div>
        {readable && slot ? (
          <ConversationSlotPane
            agent={agent}
            conversationId={conversation.id}
            slot={slot}
            embedded
            onClose={() => onSelectSlot(null)}
          />
        ) : null}
      </div>
    </Pane>
  );
}

function NotShared() {
  return <PaneNote>This conversation is not shared with this account.</PaneNote>;
}

function PaneNote({ children }: { children: React.ReactNode }) {
  return (
    <Pane className={COLUMN}>
      <div className="m-auto max-w-empty text-center text-ink-soft">{children}</div>
    </Pane>
  );
}

const RAIL_PANEL_ID = "conversation-settings-flyout";

/** The conversations header and the settings flyout it holds: the same band, the same hover, and
 *  the same panel the Applications header carries, so the two sections read and act as one
 *  system. The rows adjust the rail in place, so the panel stays up while the member reads what
 *  each press did. */
function RailSettingsFlyout({
  sort,
  onSort,
  shown,
  onShown,
}: {
  sort: RailSort;
  onSort: (sort: RailSort) => void;
  shown: RailShown;
  onShown: (shown: RailShown) => void;
}) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const dismiss = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", dismiss);
    return () => document.removeEventListener("keydown", dismiss);
  }, [open]);
  return (
    <div
      className="relative"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        aria-expanded={open}
        aria-controls={RAIL_PANEL_ID}
        onClick={() => setOpen((held) => !held)}
        className={cn(
          "flex h-(--size-row) w-full items-center gap-sm rounded-control border-0",
          "bg-transparent px-sm text-left font-sans text-label font-medium text-ink-soft",
          "hover:bg-fill",
        )}
      >
        <span className="min-w-0 flex-1 truncate">Conversations</span>
        <IconAdjustments className="size-(--size-glyph) shrink-0" aria-hidden />
      </button>
      {open ? (
        <div
          id={RAIL_PANEL_ID}
          className={cn(
            "absolute left-full top-0 z-10 flex min-w-(--container-menu) flex-col gap-px px-sm py-sm",
            "rounded-menu border border-edge bg-popover text-popover-foreground animate-raise",
            "max-narrow:left-0 max-narrow:top-full",
          )}
        >
          <PanelSub label="Sort by">
            <PanelRow active={sort === "recency"} onPick={() => onSort("recency")}>
              Recency
            </PanelRow>
            <PanelRow active={sort === "agent"} onPick={() => onSort("agent")}>
              App
            </PanelRow>
          </PanelSub>
          <h3 className="m-0 flex h-(--size-row) shrink-0 items-center px-sm font-sans text-label font-medium text-ink-soft">
            Show
          </h3>
          {RAIL_SHOWN_OPTIONS.map((option) => (
            <PanelRow
              key={option.surface}
              active={shown[option.surface]}
              onPick={() => onShown({ ...shown, [option.surface]: !shown[option.surface] })}
            >
              {option.label}
            </PanelRow>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** A nested menu inside a flyout panel: the row names the group, and resting on it opens the
 *  group's own panel beside it, the way the panel itself opened beside its header. */
function PanelSub({ label, children }: { label: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div
      className="relative"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((held) => !held)}
        className="flex h-(--size-row) w-full items-center gap-sm rounded-row border-0 bg-transparent px-sm text-left text-label text-ink-soft hover:bg-fill"
      >
        <span className="min-w-0 flex-1 truncate">{label}</span>
        <IconChevronRight className="size-icon shrink-0" aria-hidden />
      </button>
      {open ? (
        /* Placed the way the menu primitives place a submenu: past the parent panel's outer edge
           — the row's own inset plus the same small offset — with the first row level with its
           trigger, never over the parent. The offset is padding on the carrier rather than
           distance, so the pointer's path from the row to the panel never leaves the hover
           surface. */
        <div
          className="absolute left-full -top-sm z-10 pl-[calc(var(--spacing-sm)+var(--spacing-2xs))]"
        >
          <div
            className={cn(
              "flex min-w-(--container-menu) flex-col gap-px px-sm py-sm",
              "rounded-menu border border-edge bg-popover text-popover-foreground animate-raise",
            )}
          >
            {children}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function PanelRow({
  active,
  onPick,
  children,
}: {
  active: boolean;
  onPick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onPick}
      className="flex h-(--size-row) w-full shrink-0 items-center gap-sm rounded-row border-0 bg-transparent px-sm text-left text-label text-inherit hover:bg-fill"
    >
      <span className="min-w-0 flex-1 truncate">{children}</span>
      <IconCheck className={cn("size-icon shrink-0", !active && "invisible")} aria-hidden />
    </button>
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

function RailList({
  rail,
  route,
  mainAgent,
  sort,
  shown,
  shut,
  onShut,
  onOpen,
  onRetry,
}: {
  rail: Rail;
  route: Route;
  mainAgent: Agent | null;
  sort: RailSort;
  shown: RailShown;
  shut: string[] | null;
  onShut: (shut: string[]) => void;
  onOpen: (conversationId: string) => void;
  onRetry: () => void;
}) {
  const now = new Date();
  const groups = railGroups(rail.rows, sort, shown, now);
  const standing = railShut(
    shut,
    groups.map((group) => group.label),
  );
  return (
    <>
      {rail.phase === "loading" ? (
        <div className="p-sm text-ink-soft">Loading…</div>
      ) : null}
      {rail.phase === "failed" ? (
        <div className="flex flex-col gap-2xs p-sm text-ink-soft">
          <span>Couldn't load conversations.</span>
          <button
            type="button"
            onClick={onRetry}
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
          open={!standing.includes(group.label)}
          onOpenChange={(open) =>
            onShut(
              open
                ? standing.filter((label) => label !== group.label)
                : [...standing, group.label],
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
                        current={
                          (route.kind === "chat" || route.kind === "conversation-slot") &&
                          route.conversationId === row.conversation_id
                        }
                        facts={facts.length ? facts.join(" · ") : null}
                        onClick={() => onOpen(row.conversation_id)}
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

