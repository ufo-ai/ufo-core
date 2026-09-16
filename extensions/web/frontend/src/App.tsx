import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from "react";
import {
  IconChevronDown,
  IconCirclePlus,
  IconLogout,
  IconPlug,
  IconPlus,
  IconUsersGroup,
} from "@tabler/icons-react";

import { MemberAvatar, faceName } from "@/lib/memberFace";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SILENT, Toast } from "@/components/ui/toast";
import { MinimalSidebar } from "@/components/MinimalSidebar";
import { DrawerHost, useDrawerList } from "@/kernel/drawer";
import { PaneFault } from "@/kernel/pane";
import type { Seek } from "@/kernel/slots";
import { AppsProvider } from "@/lib/apps";
import { cn } from "@/lib/cn";
import {
  fleetingHomeLane,
  homeConversationLane,
  homeLaneAgent,
  homeLaneConversation,
  mintHomeLane,
  openHomeWithConnectors,
} from "@/lib/homeLanes";
import { CHAT_SURFACE, chatSurface } from "@/lib/mainAgent";
import { pickPinned, pickSectionShut, quietRail, railActivity, useRail } from "@/lib/railStore";
import {
  COMPOSING,
  CONNECTION_TABS,
  standing,
  type Route,
  type Section,
  type WorkspacePlace,
} from "@/lib/route";
import {
  forwardAgents,
  openAgent,
  openAgents,
  openBuilder,
  openNewChat,
  placeAgents,
  placeHome,
  placeSection,
  placeWorkspace,
} from "@/lib/router";
import { SCHEME_OPTIONS, pickScheme, useScheme } from "@/lib/scheme";
import { useOfferedTabs, useSurfaces } from "@/lib/surfaces";
import { heldTrack } from "@/lib/tracks";
import type { Agent, Member } from "@/lib/types";
import { AppsIndex } from "@/views/AppsIndex";
import { CONNECTORS } from "@/views/registry";
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
  founded,
  useAppsWithheld,
  useCrumb,
} from "@/views/routed";
import {
  AccountMenu,
  GLYPH,
  NarrowBar,
  PaneLoading,
  SchemeGlyph,
  ShellProviders,
  signOut,
  useShell,
  type AppProps,
} from "@/views/shell";
import { Shortcuts } from "@/views/Shortcuts";

const Home = lazy(() => import("@/views/Home").then((module) => ({ default: module.Home })));

export function App({ agents, archived = [], member, surfaces, onAgents }: AppProps) {
  const { route, rail, narrow, menu, setMenu, shutMenu, mainAgent, listed, chrome } = useShell(
    agents,
    onAgents,
    fleetingHomeLane,
  );
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
            const speaks = chatSurface(agents) ?? mainAgent;
            if (!speaks) return;
            openHomeWithConnectors(
              conversationId ? homeConversationLane(conversationId) : mintHomeLane(speaks.id, []),
            );
          }}
        />
      </ShellProviders>
    );
  }

  return (
    <ShellProviders member={member} surfaces={surfaces} agents={agents} onAgents={onAgents}>
      <Shortcuts />
      <DrawerHost hosted={narrow && chrome} shut={shutMenu}>
        <div
          className={cn(
            "grid h-dvh",
            chrome
              ? "max-narrow:grid-cols-1 max-narrow:grid-rows-[auto_1fr] grid-cols-[var(--container-minirail)_1fr]"
              : "max-narrow:grid-cols-1 grid-cols-[var(--container-minirail)_1fr]",
          )}
        >
          {chrome && narrow ? (
            <NarrowBar agents={listed} member={member} menu={menu} onMenu={setMenu} />
          ) : null}
          {chrome && narrow ? (
            <WorkspaceSidebar route={route} agents={listed} member={member} mainAgent={mainAgent} />
          ) : null}
          {!narrow ? (
            <MinimalSidebar
              lanes={homeLanes}
              active={activeLane}
              agents={listed}
              member={member}
              main={mainAgent}
              account={<AccountMenu member={member} />}
              onLane={(lane) => {
                if (!homeOpens.includes(lane)) return;
                enterLane(lane, homeOpens);
              }}
              onBuild={openBuilder}
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
                  seeking={seeking}
                  onActive={setActiveLane}
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

const AskGlyph = () => <IconPlus className={GLYPH} aria-hidden />;
const CreateAppGlyph = () => <IconCirclePlus className={GLYPH} aria-hidden />;
const WorkspaceGlyph = () => <IconUsersGroup className={GLYPH} aria-hidden />;

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
    <div className="group/head flex h-(--size-row) w-full shrink-0 items-center rounded-control hover:bg-fill">
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

function building(route: Route): boolean {
  return route.kind === "agents" && route.build === true;
}

function WorkspaceSidebar({
  route,
  agents,
  member,
  mainAgent,
}: {
  route: Route;
  agents: Agent[];
  member: Member;
  mainAgent: Agent | null;
}) {
  const rail = useRail();
  const tabs = useOfferedTabs();
  const surfaces = useSurfaces();
  const appsShut = rail.sectionsShut.includes(APPS);
  const pinned = rail.pinned ?? defaultPins(agents);
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
                onClick={() => openNewChat(mainAgent.id)}
              >
                {NEW_CHAT}
              </NavRow>
            </li>
            {surfaces.apps ? (
              <li>
                <NavRow icon={<CreateAppGlyph />} current={building(route)} onClick={openBuilder}>
                  {CREATE_APP}
                </NavRow>
              </li>
            ) : null}
          </>
        ) : null}
      </ul>
      {surfaces.apps ? (
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
              building={building(route)}
              pinned={pinned}
              onPin={(agentId) =>
                pickPinned(
                  pinned.includes(agentId)
                    ? pinned.filter((id) => id !== agentId)
                    : [...pinned, agentId],
                )
              }
              onOpen={openAgent}
              onBuild={openBuilder}
            />
          )}
        </div>
      ) : null}
      <ul className="m-0 mt-auto flex shrink-0 list-none flex-col gap-px px-sm py-0">
        <li>
          <NavRow
            icon={SECTION_GLYPHS.connectors}
            current={CONNECTION_TABS.some((tab) => standing(route, `section:${tab}`))}
            onClick={() => placeSection(CONNECTION_TABS[0], {}, "push")}
          >
            {CONNECTORS.label}
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
      <footer className="flex shrink-0 items-center gap-sm px-lg">
        <MemberAvatar face={member} />
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="truncate text-label">{faceName(member)}</span>
          <span className="truncate text-small text-ink-soft">{member.email}</span>
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

/** The kinds this shell renders itself: home stands lanes, and the agents address lists the apps.
 *  Every other kind is the pane the two shells share, and one this shell does not host is a bad link. */
function RoutedPane({
  route,
  agents,
  member,
  mainAgent,
  seeking,
  onActive,
  onAgents,
}: {
  route: Route;
  agents: Agent[];
  member: Member;
  mainAgent: Agent | null;
  seeking: Seek | undefined;
  onActive: (lane: string | undefined) => void;
  onAgents: () => void;
}) {
  const crumb = useCrumb(route, agents, mainAgent);
  if (useAppsWithheld(route)) return <InvalidLink />;
  switch (route.kind) {
    case "agent-setup":
      return <SetupPane route={route} agents={agents} crumb={crumb} onAgents={onAgents} />;
    case "bad-link":
    case "chats":
      return <InvalidLink />;
    case "store":
      return <StorePane member={member} />;
    case "automations":
      return <AutomationsPane route={route} crumb={crumb} />;
    case "workspace":
      return <WorkspacePane view={route.view} place={route.place} crumb={crumb} />;
    case "section":
      return <SectionPane route={route} agents={agents} crumb={crumb} />;
    case "agents":
      if (!building(route))
        return (
          <WorkspacePane
            view="apps"
            place={route.place}
            crumb={crumb}
            onPlace={(view, place, step) =>
              view === "apps" ? placeAgents(place, step) : placeWorkspace(view, place, step)
            }
          />
        );
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
      return (
        <Home
          place={route.place}
          agents={agents}
          member={member}
          mainAgent={mainAgent}
          seeking={seeking}
          onActive={onActive}
          onFounded={(agent, conversationId, title) =>
            founded(agent, member, conversationId, title)
          }
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
      if (!agent) return <NoSuchApp />;
      return <NewChatPane agent={agent} member={member} />;
    }
  }
  const missed: never = route;
  return missed;
}
