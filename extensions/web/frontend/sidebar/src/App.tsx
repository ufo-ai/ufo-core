import {
  Suspense,
  lazy,
  useCallback,
  useEffect,
  useId,
  useState,
} from "react";
import {
  IconCirclePlusFilled,
  IconClockPlay,
  IconDotsVertical,
  IconFile,
  IconFilter2,
  IconHome,
  IconLayoutSidebarRight,
  IconPencilPlus,
  IconPlug,
  IconRadar,
  IconSettings,
} from "@tabler/icons-react";

import mark from "@brand/ufo-mark.svg";
import {
  SIDEBAR_PRESS,
  SIDEBAR_ROW,
  SidebarCap,
  SidebarPress,
  SidebarRow,
  SidebarTooltip,
  type Chord,
} from "@/components/Sidebar";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Skeleton } from "@/components/ui/skeleton";

import { Ticker } from "@/components/ui/ticker";
import { SILENT, Toast } from "@/components/ui/toast";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";

import { Spotlight } from "@/views/Spotlight";
import {
  AgentsPane,
  AutomationsPane,
  ChatRoutePane,
  FirstRunPane,
  InvalidLink,
  NewChatPane,
  NoSuchApp,
  SectionPane,
  SetupPane,
  SlotPane,
  StorePane,
  WorkspacePane,
  useAppsWithheld,
  useCrumb,
} from "@/views/routed";
import {
  AccountActs,
  GLYPH,
  NarrowBar,
  PaneLoading,
  ShellProviders,
  useShell,
  type AppProps,
} from "@/views/shell";
import {
  isPortalChat,
  origin,
  slackLink,
  surfaceWord,
  speakerName,
} from "@/lib/audience";

import { AppsProvider } from "@/lib/apps";

import {DrawerHost, useDrawerHost, useDrawerList} from "@/kernel/drawer";
import {PaneFault} from "@/kernel/pane";

import { agentName } from "@/lib/agentName";
import { ChatMark } from "@/lib/chatMark";

import { rowMoment } from "@/lib/moments";
import { cn } from "@/lib/cn";
import {HOME_TITLE} from "@/lib/title";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  RAIL_SHOWN_OPTIONS,
  RAIL_SORT_OPTIONS,
  railRows,
  type RailSort,
} from "@/lib/rail";
import {
  foldSidebar,
  pickRailSort,
  pickRailShown,
  quietRail,
  railRead,
  readRail,
  useRail,
} from "@/lib/railStore";
import {
  forwardAgents,
  openAgents,
  openChat,
  openChats,
  placeChats,
  openNewChat,
  openAutomations,
  placeSection,
  placeWorkspace,
} from "@/lib/router";
import {
  chatHash,
  COMPOSING,
  CONNECTION_TABS,
  standing,
  type Route,
  type Section,
} from "@/lib/route";
import {useOfferedTabs, useSurfaces} from "@/lib/surfaces";
import type { Agent, ConversationTurn, Member } from "@/lib/types";

const Chats = lazy(() => import("@/views/Chats").then((module) => ({ default: module.Chats })));

export function App({ agents, archived = [], member, surfaces, onAgents }: AppProps) {
  const { route, rail, narrow, menu, setMenu, shutMenu, mainAgent, listed, chrome } = useShell(
    agents,
    onAgents,
  );

  if (route.kind === "first-run") {
    return (
      <ShellProviders member={member} surfaces={surfaces} agents={agents} onAgents={onAgents}>
        <FirstRunPane
          agents={agents}
          member={member}
          mainAgent={mainAgent}
          step={route.step}
          onClose={() => {
            if (mainAgent) openNewChat(mainAgent.id);
          }}
          onDone={(conversationId) => {
            if (conversationId) openChat(conversationId);
            else if (mainAgent) openNewChat(mainAgent.id);
          }}
        />
      </ShellProviders>
    );
  }

  return (
    <ShellProviders member={member} surfaces={surfaces} agents={agents} onAgents={onAgents}>
      <DrawerHost hosted={narrow && chrome} shut={shutMenu}>
        <div
          className={cn(
            "grid h-dvh",
            chrome
              ? cn(
                  "max-narrow:grid-cols-1 max-narrow:grid-rows-[auto_1fr]",
                  rail.collapsed
                    ? "grid-cols-[var(--container-rail)_1fr]"
                    : "grid-cols-[var(--container-sidebar)_1fr]",
                )
              : "grid-cols-1",
          )}
        >
          {chrome && narrow ? (
            <NarrowBar agents={listed} member={member} menu={menu} onMenu={setMenu} />
          ) : null}
          {chrome ? (
            <WorkspaceSidebar
              route={route}
              agents={listed}
              member={member}
              mainAgent={mainAgent}
              narrow={narrow}
            />
          ) : null}
          <AppsProvider agents={listed} archived={archived} onRestored={onAgents}>
            <PaneFault at={route.kind}>
              <Suspense fallback={<PaneLoading />}>
                <RoutedPane
                  route={route}
                  agents={agents}
                  member={member}
                  mainAgent={mainAgent}
                  onAgents={onAgents}
                />
              </Suspense>
            </PaneFault>
          </AppsProvider>
          <Toast state={rail.fault ?? SILENT} onDone={quietRail} />
        </div>
      </DrawerHost>
    </ShellProviders>
  );
}

/** The account stands as a row of the sidebar rather than as a strip of glyphs under it: it takes
 *  the width and the hover of every other row, and its acts live in the menu it opens. */
function AccountRow({ member, collapsed }: { member: Member; collapsed: boolean }) {
  const host = useDrawerHost();
  return (
    <DropdownMenu>
      <SidebarRow>
        <SidebarTooltip collapsed={collapsed} label={member.email}>
          <DropdownMenuTrigger asChild>
            <SidebarPress
              collapsed={collapsed}
              label={member.email}
              aria-label={member.email}
              glyph={
                <Avatar>
                  <AvatarFallback>{member.email.slice(0, 1).toUpperCase()}</AvatarFallback>
                </Avatar>
              }
            >
              <span className="min-w-0 flex-1 truncate">{member.email}</span>
              <IconDotsVertical className={cn(GLYPH, "text-ink-soft")} aria-hidden />
            </SidebarPress>
          </DropdownMenuTrigger>
        </SidebarTooltip>
      </SidebarRow>
      <DropdownMenuContent side="top" align="start" container={host}>
        <AccountActs container={host} />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

const SETTINGS_LABEL = "Settings";
const RECENTS = "Recents";

/** The placeholder rows are drawn for the eye alone, so the list states the wait for a reader who
 *  hears the page instead. */
const RAIL_WAIT = "Loading chats";
const NEW_CHAT = "New chat";
const AUTOMATIONS = "Automations";
const CONNECTIONS = "Connections";

/** Shift makes the character upper case, so the guard reads the letter. */
const NEW_CHAT_CHORD: Chord = { key: "o", cap: "\u21e7\u2318O", aria: "Meta+Shift+O" };

const SECTION_HEAD_GLYPH =
  "mr-xs shrink-0 rounded-control border-0 bg-transparent p-2xs text-ink-soft hover:bg-fill opacity-0 transition-opacity duration-100 ease-control motion-reduce:transition-none group-hover/head:opacity-100 focus-visible:opacity-100 data-[state=open]:opacity-100 data-[state=open]:bg-fill";

const AskGlyph = () => <IconCirclePlusFilled className={cn(GLYPH, "text-primary")} aria-hidden />;

const SECTION_GLYPHS: Partial<Record<Section, React.ReactNode>> = {
  radar: <IconRadar className={GLYPH} aria-hidden />,
  artifacts: <IconFile className={GLYPH} aria-hidden />,
  connectors: <IconPlug className={GLYPH} aria-hidden />,
};

const APP_SECTIONS: { section: Section; label: string }[] = [
  { section: "radar", label: "Radar" },
  { section: "artifacts", label: "Artifacts" },
];

const HEADER_CONTROL = "rounded-control border-0 bg-transparent p-2xs text-ink-soft hover:bg-fill";

function SidebarToggle({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button type="button" aria-label={label} onClick={onClick} className={HEADER_CONTROL}>
      <IconLayoutSidebarRight className={GLYPH} aria-hidden />
    </button>
  );
}

function NavRow({
  icon,
  current,
  collapsed,
  label,
  chord,
  onClick,
}: {
  icon: React.ReactNode;
  current: boolean;
  collapsed: boolean;
  label: string;
  chord?: Chord;
  onClick: () => void;
}) {
  return (
    <SidebarRow current={current}>
      <SidebarTooltip collapsed={collapsed} label={label} chord={chord}>
        <SidebarPress
          current={current}
          collapsed={collapsed}
          label={label}
          glyph={icon}
          aria-keyshortcuts={chord?.aria}
          onClick={onClick}
        >
          {chord ? (
            <>
              <span className="min-w-0 flex-1 truncate">{label}</span>
              <SidebarCap chord={chord} />
            </>
          ) : undefined}
        </SidebarPress>
      </SidebarTooltip>
    </SidebarRow>
  );
}

function SectionHead({
  label,
  collapsed,
  acts,
  children,
}: {
  label: string;
  collapsed?: boolean;
  acts?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "group/head flex h-(--size-row) w-full shrink-0 items-center rounded-control px-sm",
        collapsed && "hidden",
      )}
    >
      <h2
        className={cn(
          "m-0 min-w-0 flex-1 truncate px-sm",
          "font-sans text-label font-medium text-ink-soft",
        )}
      >
        {label}
      </h2>
      {acts}
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

function WorkspaceSidebar({
  route,
  agents,
  member,
  mainAgent,
  narrow,
}: {
  route: Route;
  agents: Agent[];
  member: Member;
  mainAgent: Agent | null;
  narrow: boolean;
}) {
  const rail = useRail();
  const tabs = useOfferedTabs();
  const surfaces = useSurfaces();
  const collapsed = rail.collapsed && !narrow;
  const startChat = useCallback(() => {
    if (mainAgent) openNewChat(mainAgent.id);
  }, [mainAgent]);
  useEffect(() => {
    const chord = (event: KeyboardEvent) => {
      if (event.defaultPrevented || !event.metaKey || !event.shiftKey) return;
      if (event.altKey || event.ctrlKey) return;
      if (event.key.toLowerCase() !== NEW_CHAT_CHORD.key) return;
      event.preventDefault();
      startChat();
    };
    document.addEventListener("keydown", chord);
    return () => document.removeEventListener("keydown", chord);
  }, [startChat]);
  return useDrawerList(
    <nav
      aria-label="Workspace"
      className={cn(
        "flex min-h-0 flex-col gap-2xl border-r border-edge bg-sidebar py-xl",
        "max-narrow:flex-1 max-narrow:border-r-0 max-narrow:py-0",
      )}
    >
      <div
        className={cn(
          "flex shrink-0 items-center max-narrow:hidden",
          collapsed
            ? "flex-col justify-center gap-sm px-sm"
            : "h-(--size-row) gap-2xs pl-2xl pr-sm",
        )}
      >
        <span
          role="img"
          aria-label="ufo"
          className={cn("size-(--size-glyph) shrink-0 bg-current", collapsed && "hidden")}
          style={{ mask: `url(${mark}) center / contain no-repeat` }}
        />
        <Spotlight
          agents={agents}
          collapsed={collapsed}
          className={cn(HEADER_CONTROL, !collapsed && "ml-auto")}
        />
        <SidebarTooltip collapsed={collapsed} label="Expand sidebar">
          <SidebarToggle
            label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            onClick={() => foldSidebar(!collapsed)}
          />
        </SidebarTooltip>
      </div>
      <ul className="m-0 flex list-none flex-col gap-px p-0">
        {mainAgent ? (
          <NavRow
            icon={<AskGlyph />}
            current={standing(route, COMPOSING)}
            collapsed={collapsed}
            label={NEW_CHAT}
            chord={NEW_CHAT_CHORD}
            onClick={startChat}
          />
        ) : null}
        <NavRow
          icon={<IconHome className={GLYPH} aria-hidden />}
          current={standing(route, "chats")}
          collapsed={collapsed}
          label={HOME_TITLE}
          onClick={openChats}
        />
        <NavRow
          icon={<IconClockPlay className={GLYPH} aria-hidden />}
          current={standing(route, "automations")}
          collapsed={collapsed}
          label={AUTOMATIONS}
          onClick={openAutomations}
        />
        {APP_SECTIONS.filter(({ section }) => section !== "radar" || surfaces.radar).map(
          ({ section, label }) => (
            <NavRow
              key={section}
              icon={SECTION_GLYPHS[section]}
              current={standing(route, `section:${section}`)}
              collapsed={collapsed}
              label={label}
              onClick={() => placeSection(section, {}, "push")}
            />
          ),
        )}
        <NavRow
          icon={SECTION_GLYPHS.connectors}
          current={CONNECTION_TABS.some((tab) => standing(route, `section:${tab}`))}
          collapsed={collapsed}
          label={CONNECTIONS}
          onClick={() => placeSection(CONNECTION_TABS[0], {}, "push")}
        />
        <NavRow
          icon={<IconSettings className={GLYPH} aria-hidden />}
          current={standing(route, "workspace")}
          collapsed={collapsed}
          label={SETTINGS_LABEL}
          onClick={() => placeWorkspace(tabs[0], {}, "push")}
        />
      </ul>
      <div className={cn("flex min-h-0 flex-1 flex-col gap-px", collapsed && "hidden")}>
        <RailSettingsFlyout onCompose={startChat} />
        <div className="flex min-h-0 flex-1 flex-col gap-sm overflow-y-auto">
          <RailList route={route} mainAgent={mainAgent} />
        </div>
      </div>
      <footer className="mt-auto shrink-0">
        <ul className="m-0 flex list-none flex-col gap-px p-0">
          <AccountRow member={member} collapsed={collapsed} />
        </ul>
      </footer>
    </nav>,
  );
}

/** The kinds this shell renders itself — home as a composer with the main agent, the agents address
 *  as the main agent's screen, the chats listing — and the panes the two shells share for the rest. */
function RoutedPane({
  route,
  agents,
  member,
  mainAgent,
  onAgents,
}: {
  route: Route;
  agents: Agent[];
  member: Member;
  mainAgent: Agent | null;
  onAgents: () => void;
}) {
  const crumb = useCrumb(route, agents, mainAgent);
  if (useAppsWithheld(route)) return <InvalidLink />;
  switch (route.kind) {
    case "agent-setup":
      return <SetupPane route={route} agents={agents} crumb={crumb} onAgents={onAgents} />;
    case "bad-link":
      return <InvalidLink />;
    case "store":
      return <StorePane member={member} />;
    case "chats":
      return (
        <Chats
          place={route.place}
          onPlace={placeChats}
          onOpen={(row) => openRailRow(row.conversation_id)}
        />
      );
    case "automations":
      return <AutomationsPane route={route} crumb={crumb} />;
    case "workspace":
      return <WorkspacePane view={route.view} place={route.place} crumb={crumb} />;
    case "section":
      return <SectionPane route={route} agents={agents} crumb={crumb} />;
    case "agents":
    case "agent":
      return (
        <AgentsPane
          route={route}
          agents={agents}
          member={member}
          mainAgent={mainAgent}
          onAgents={onAgents}
          onExitBuilder={openAgents}
          onForwardAgents={forwardAgents}
        />
      );
    case "conversation-slot":
      return <SlotPane route={route} agents={agents} crumb={crumb} />;
    case "chat":
      return <ChatRoutePane route={route} agents={agents} member={member} crumb={crumb} />;
    case "home":
    case "first-run":
    case "new-chat": {
      const agent =
        route.kind === "new-chat"
          ? agents.find((entry) => entry.id === route.agentId)
          : (mainAgent ?? undefined);
      if (!agent) return <NoSuchApp />;
      return <NewChatPane agent={agent} member={member} />;
    }
  }
  const missed: never = route;
  return missed;
}

function RailSettingsFlyout({ onCompose }: { onCompose: () => void }) {
  const { shown, sort } = useRail();
  const host = useDrawerHost();
  return (
    <SectionHead
      label={RECENTS}
      acts={
        <button
          type="button"
          aria-label={NEW_CHAT}
          onClick={onCompose}
          className={SECTION_HEAD_GLYPH}
        >
          <IconPencilPlus className="size-icon" aria-hidden />
        </button>
      }
    >
      <DropdownMenuContent side="right" align="start" container={host}>
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
        <DropdownMenuSeparator />
        <DropdownMenuLabel>Sort</DropdownMenuLabel>
        <DropdownMenuRadioGroup
          value={sort}
          onValueChange={(next) => pickRailSort(next as RailSort)}
        >
          {RAIL_SORT_OPTIONS.map((option) => (
            <DropdownMenuRadioItem key={option.sort} value={option.sort}>
              {option.label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </SectionHead>
  );
}

/** Routed through an agent's page, a conversation landed on whatever site the workspace had bound
 *  as that page — a member-built homepage with no transcript on it — so it opens as itself. */
function openRailRow(conversationId: string): void {
  railRead(conversationId);
  openChat(conversationId);
}

const RAIL_SKELETON_TITLES = ["w-4/5", "w-3/5", "w-3/4", "w-1/2", "w-2/3", "w-2/5"];

/** The rows take the shape of the chat rows they wait for, so the list does not step when the
 *  titles land. The bar widths vary because one width for every row reads as a control. */
function RailSkeleton() {
  return (
    <ul role="status" aria-label={RAIL_WAIT} className="m-0 flex list-none flex-col gap-px p-0">
      {RAIL_SKELETON_TITLES.map((title) => (
        <li key={title} className={SIDEBAR_ROW}>
          <div className={cn(SIDEBAR_PRESS, "gap-xs")}>
            <Skeleton className="me-2xs size-(--size-glyph) shrink-0 rounded-full" />
            <Skeleton className={cn("h-(--size-notice)", title)} />
          </div>
        </li>
      ))}
    </ul>
  );
}

function RailList({ route, mainAgent }: { route: Route; mainAgent: Agent | null }) {
  const rail = useRail();
  const rows = railRows(rail.rows, rail.shown, rail.sort);
  const list = (
    <ul className="m-0 flex list-none flex-col gap-px p-0">
      {rows.map((row) => {
        const facts = [
          row.speaker ? speakerName(row.speaker) : null,
          isPortalChat(row.surface) ? null : origin(row),
          mainAgent && row.agent_id !== mainAgent.id ? agentName(row.agent_name) : null,
        ].filter((fact): fact is string => fact !== null);
        return (
          <RailRow
            key={row.conversation_id}
            current={standing(route, `open:${row.conversation_id}`)}
            conversationId={row.conversation_id}
            facts={facts}
            title={row.title}
            opening={row.opening}
            surface={row.surface}
            surfaceLabel={row.surface_label}
            source={row.source}
            turn={row.turn}
            automated={row.automation_name !== null}
            unread={row.unread}
            when={row.last_at}
            onClick={() => openRailRow(row.conversation_id)}
          />
        );
      })}
    </ul>
  );
  return (
    <>
      {rail.phase === "loading" ? <RailSkeleton /> : null}
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
      {rows.length ? list : null}
    </>
  );
}

/** The whole row is one thing under the pointer, and a menu left open holds it: the pointer that
 *  opened the menu has gone to the menu. */
function RailRow({
  current,
  conversationId,
  facts,
  title,
  opening,
  surface,
  surfaceLabel,
  source,
  turn,
  automated,
  unread,
  when,
  onClick,
}: {
  current: boolean;
  conversationId: string;
  facts: string[];
  title: string;
  opening: string | null;
  surface: string;
  surfaceLabel: string | null;
  source: string | null;
  turn: ConversationTurn;
  automated: boolean;
  unread: boolean;
  when: string;
  onClick: () => void;
}) {
  const [asks, setAsks] = useState(0);
  const [acts, setActs] = useState(false);
  const card = useId();
  const reached = asks > 0 || acts;
  const button = (
    <SidebarPress
      current={current}
      label={title}
      className="gap-xs select-none"
      aria-describedby={card}
      onClick={onClick}
      onFocus={() => setAsks((asked) => asked + 1)}
      onBlur={() => setAsks(0)}
      glyph={
        <ChatMark
          surface={surface}
          turn={turn}
          automated={automated}
          unread={unread}
          className="me-2xs"
        />
      }
    >
      <Ticker asks={asks + (acts ? 1 : 0)} className={cn("flex-1", reached && "me-6xl")}>
        {title}
      </Ticker>
    </SidebarPress>
  );
  return (
    <HoverCard>
      <HoverCardTrigger asChild>
        <SidebarRow
          current={current || acts}
          onPointerEnter={() => setAsks((asked) => asked + 1)}
          onPointerLeave={() => setAsks(0)}
        >
          {button}
          <RailRowActs
            conversationId={conversationId}
            surface={surface}
            surfaceLabel={surfaceLabel}
            source={source}
            open={acts}
            onOpenChange={setActs}
          />
        </SidebarRow>
      </HoverCardTrigger>
      <HoverCardContent id={card}>
        <span className="text-balance font-medium leading-snug">{title}</span>
        {opening ? <span className="text-pretty leading-snug text-ink-soft">{opening}</span> : null}
        <span className="text-small text-ink-faint">
          {[...facts, rowMoment(when, new Date())].join(" · ")}
        </span>
      </HoverCardContent>
    </HoverCard>
  );
}

/** The mark names the acts alone: a name carrying the title would answer every read that looks the
 *  row up by its own words. */
const THREAD_ACTS = "Thread options";

const RAIL_ROW_GLYPH = cn(
  "absolute end-xs top-1/2 -translate-y-1/2 rounded-control border-0 bg-fill p-hair text-ink-soft",
  "opacity-0 transition-opacity duration-100 ease-control motion-reduce:transition-none",
  "group-hover/row:opacity-100 group-has-[:focus-visible]/row:opacity-100",
  "focus-visible:opacity-100 data-[state=open]:opacity-100",
);

/** The menu stands beside the row's one press rather than inside it, so a pick of an act never opens
 *  the thread, and over the row's end rather than in its flow, so it holds width off no title. */
function RailRowActs({
  conversationId,
  surface,
  surfaceLabel,
  source,
  open,
  onOpenChange,
}: {
  conversationId: string;
  surface: string;
  surfaceLabel: string | null;
  source: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const host = useDrawerHost();
  const away = slackLink(surface, source);
  return (
    <DropdownMenu open={open} onOpenChange={onOpenChange}>
      <DropdownMenuTrigger asChild>
        <button type="button" aria-label={THREAD_ACTS} className={RAIL_ROW_GLYPH}>
          <IconDotsVertical className="size-icon" aria-hidden />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="bottom" align="end" container={host}>
        <DropdownMenuItem
          onSelect={() => void navigator.clipboard.writeText(threadLink(conversationId))}
        >
          Copy link
        </DropdownMenuItem>
        {away === null ? null : (
          <DropdownMenuItem asChild>
            <a
              href={away}
              target="_blank"
              rel="noopener noreferrer"
              className="text-inherit no-underline"
            >
              Open in {surfaceLabel ?? surfaceWord(surface)}
            </a>
          </DropdownMenuItem>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function threadLink(conversationId: string): string {
  return window.location.origin + window.location.pathname + chatHash(conversationId);
}
