import { Suspense, lazy, useEffect } from "react";

import { COLUMN, Header, Pane, PaneFault, PaneNote } from "@/kernel/pane";
import { MEMBER_SUBJECT, WEB_SURFACE } from "@/lib/audience";
import { cn } from "@/lib/cn";
import { useNarrow } from "@/lib/narrow";
import { stampIso } from "@/lib/rail";
import { railActivity, railFounded, useRail } from "@/lib/railStore";
import {
  CONNECTION_TABS,
  connectionTab,
  type PlaceStep,
  type Route,
  type Section,
  type WorkspacePlace,
  type WorkspaceTab,
} from "@/lib/route";
import {
  buildWanted,
  heldRoute,
  openAgentPlace,
  openBuilder,
  openChat,
  openSlot,
  placeAgent,
  placeAutomations,
  placeFirstRun,
  placeSection,
  placeWorkspace,
} from "@/lib/router";
import { useOfferedTabs, useSurfaces } from "@/lib/surfaces";
import { SETUP, pageCrumb, type Crumb } from "@/lib/title";
import type { Agent, Member } from "@/lib/types";
import { SignIn } from "@/views/SignIn";
import { TabbedPane } from "@/views/TabbedPane";
import {
  AUTOMATIONS_TAB,
  AUTOMATIONS_VIEWS,
  CONNECTION_VIEWS,
  SECTION_VIEWS,
  WORKSPACE_VIEWS,
  type PaneView,
} from "@/views/registry";
import { PaneLoading } from "@/views/shell";

/** One route's pane is one chunk: a member who opens the wizard, the store or a workspace tab never
 *  downloads the transcript renderer, and the shell paints before any of them arrives. */
const AgentSetup = lazy(() =>
  import("@/views/AgentSetup").then((module) => ({ default: module.AgentSetup })),
);
const Agents = lazy(() => import("@/views/Agents").then((module) => ({ default: module.Agents })));
const ChatPane = lazy(() =>
  import("@/views/ChatPane").then((module) => ({ default: module.ChatPane })),
);
const ConversationSlotPane = lazy(() =>
  import("@/views/ConversationSlotPane").then((module) => ({
    default: module.ConversationSlotPane,
  })),
);
const FirstRun = lazy(() =>
  import("@/views/FirstRun").then((module) => ({ default: module.FirstRun })),
);
const Store = lazy(() => import("@/views/Store").then((module) => ({ default: module.Store })));

type Of<Kind extends Route["kind"]> = Extract<Route, { kind: Kind }>;

/** A conversation this tab founded stands in the rail at once, and a composer that stood on the
 *  agent's new-chat address moves to it. */
export function founded(
  agent: Agent,
  member: Member,
  conversationId: string,
  title: string,
): void {
  if (member.id === undefined) throw new Error("member id missing");
  const at = stampIso(new Date());
  const audience = MEMBER_SUBJECT + member.id;
  railFounded({
    conversation_id: conversationId,
    agent_id: agent.id,
    agent_name: agent.name,
    title,
    opening: null,
    last_at: at,
    surface: WEB_SURFACE,
    surface_label: null,
    audience,
    member_email: member.email,
    owner_email: member.email,
    owner_name: null,
    mine: true,
    speaker: null,
    source: null,
    turn: "running",
    automation_kind: null,
    automation_name: null,
    automation_title: null,
    unread: false,
    artifacts: false,
    speakers: [member.email],
    speaker_emails: [member.email],
    turn_count: 1,
    created_at: at,
    last_turn_at: at,
    archived: false,
    deleted: false,
    pinned: false,
    readable: true,
    disclosable: false,
    speakable: true,
  });
  const seen = heldRoute();
  if (seen.kind === "new-chat" && seen.agentId === agent.id) openChat(conversationId);
}

export function InvalidLink() {
  return <PaneNote>This link is not valid.</PaneNote>;
}

export function NoSuchApp() {
  return <PaneNote>No such app.</PaneNote>;
}

export function NotShared() {
  return <PaneNote>This conversation is not shared with this account.</PaneNote>;
}

export function useCrumb(
  route: Route,
  agents: Agent[],
  mainAgent: Agent | null,
): Crumb | undefined {
  const rail = useRail();
  return pageCrumb(route, agents, rail.known, mainAgent);
}

/** The apps flag withholds every address that lists, builds or shops for apps. */
export function useAppsWithheld(route: Route): boolean {
  const surfaces = useSurfaces();
  const appIndex =
    route.kind === "agents" ||
    route.kind === "store" ||
    (route.kind === "workspace" && route.view === "apps");
  return appIndex && !surfaces.apps;
}

export function SetupPane({
  route,
  agents,
  crumb,
  onAgents,
}: {
  route: Of<"agent-setup">;
  agents: Agent[];
  crumb: Crumb | undefined;
  onAgents: () => void;
}) {
  const app = agents.find((entry) => entry.id === route.agentId) ?? null;
  if (!app) return <NoSuchApp />;
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

export function StorePane({ member }: { member: Member }) {
  return <Store member={member} onBuild={openBuilder} />;
}

export function AutomationsPane({
  route,
  crumb,
}: {
  route: Of<"automations">;
  crumb: Crumb | undefined;
}) {
  return (
    <TabbedPane
      group="automations"
      tabs={[AUTOMATIONS_TAB]}
      views={AUTOMATIONS_VIEWS}
      view={AUTOMATIONS_TAB}
      crumb={crumb}
      place={route.place}
      onPlace={(_tab, place, step) => placeAutomations(place, step)}
    />
  );
}

export function WorkspacePane({
  view,
  place,
  crumb,
  onPlace = placeWorkspace,
}: {
  view: WorkspaceTab;
  place: WorkspacePlace;
  crumb: Crumb | undefined;
  onPlace?: (view: WorkspaceTab, place: WorkspacePlace, step: PlaceStep) => void;
}) {
  const tabs = useOfferedTabs();
  return (
    <TabbedPane
      group="workspace"
      tabs={tabs}
      views={WORKSPACE_VIEWS}
      view={view}
      crumb={crumb}
      place={place}
      onPlace={onPlace}
    />
  );
}

function SectionLanding({ agentId, place }: { agentId: string; place: WorkspacePlace }) {
  useEffect(() => openAgentPlace(agentId, place), [agentId, place]);
  return null;
}

export function SectionPane({
  route,
  agents,
  crumb,
}: {
  route: Of<"section">;
  agents: Agent[];
  crumb: Crumb | undefined;
}) {
  if (connectionTab(route.section)) {
    return (
      <TabbedPane
        group="connections"
        tabs={CONNECTION_TABS}
        views={CONNECTION_VIEWS}
        view={route.section}
        place={route.place}
        onPlace={placeSection}
      />
    );
  }
  const view = SECTION_VIEWS[route.section];
  if (view === undefined) {
    const shipped = agents.find((agent) => agent.app === route.section);
    if (!shipped) return <InvalidLink />;
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

/** An agent's own screen, or the main agent's where the address names none; `agents` with `build`
 *  stands the wizard in the pane instead. */
export function AgentsPane({
  route,
  agents,
  member,
  mainAgent,
  onAgents,
  onExitBuilder,
  onForwardAgents,
}: {
  route: Of<"agent" | "agents">;
  agents: Agent[];
  member: Member;
  mainAgent: Agent | null;
  onAgents: () => void;
  onExitBuilder: () => void;
  onForwardAgents: () => void;
}) {
  const rail = useRail();
  const selected =
    route.kind === "agent" ? (agents.find((entry) => entry.id === route.agentId) ?? null) : null;
  if (route.kind === "agent" && !selected) return <NoSuchApp />;
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
        onCreated={(agent, conversationId, title) => founded(agent, member, conversationId, title)}
        place={route.kind === "agent" ? route.place : {}}
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
        buildWanted={buildWanted()}
      />
    </Pane>
  );
}

export function SlotPane({
  route,
  agents,
  crumb,
}: {
  route: Of<"conversation-slot">;
  agents: Agent[];
  crumb: Crumb | undefined;
}) {
  const rail = useRail();
  const agent = agents.find((entry) => entry.id === route.agentId);
  if (!agent) return <NoSuchApp />;
  return (
    <ConversationSlotPane
      agent={agent}
      conversationId={route.conversationId}
      slot={route.slot}
      rootConversationId={route.rootConversationId}
      audience={rail.known[route.conversationId]}
      crumb={crumb}
    />
  );
}

export function ChatRoutePane({
  route,
  agents,
  member,
  crumb,
}: {
  route: Of<"chat">;
  agents: Agent[];
  member: Member;
  crumb: Crumb | undefined;
}) {
  const rail = useRail();
  const narrow = useNarrow();
  const outcome = rail.sought[route.conversationId];
  const refused = outcome !== undefined && outcome.kind !== "failed";
  const conversation = refused ? undefined : rail.known[route.conversationId];
  if (!conversation) {
    if (!outcome) return <PaneLoading />;
    if (outcome.kind === "absent") return <NotShared />;
    if (outcome.kind === "signed-out") {
      return (
        <Pane className={COLUMN}>
          <div className="m-auto">
            <SignIn />
          </div>
        </Pane>
      );
    }
    return <PaneNote>{outcome.message}</PaneNote>;
  }
  if (!conversation.readable && !conversation.disclosable) return <NotShared />;
  const listedAgent = agents.find((entry) => entry.id === conversation.agent_id);
  const agent =
    listedAgent ??
    (conversation.surface.startsWith("extension:")
      ? { id: conversation.agent_id, name: conversation.agent_name, model: "" }
      : undefined);
  if (!agent) return <NoSuchApp />;
  return (
    <ChatPane
      key={conversation.conversation_id}
      agent={agent}
      member={member}
      conversationId={conversation.conversation_id}
      conversation={conversation}
      focusComposer={!narrow}
      onActivity={railActivity}
      conversationOnly={!listedAgent}
      crumb={crumb}
      slot={route.slot}
      onSelectSlot={(slot) => openSlot(route.conversationId, slot)}
    />
  );
}

/** A composer standing on the home address opens the conversation it founds; one on the agent's
 *  new-chat address is moved by `founded` itself. A key carrying the agent would remount the box on
 *  every rename — a fresh box holds the draft again but not the member's place in it. */
export function NewChatPane({ agent, member }: { agent: Agent; member: Member }) {
  return (
    <ChatPane
      key="new"
      agent={agent}
      member={member}
      conversationId={null}
      focusComposer
      onCreated={(conversationId, title) => {
        founded(agent, member, conversationId, title);
        if (heldRoute().kind === "home") openChat(conversationId);
      }}
      onActivity={railActivity}
    />
  );
}

export function FirstRunPane({
  agents,
  member,
  mainAgent,
  step,
  onClose,
  onDone,
}: {
  agents: Agent[];
  member: Member;
  mainAgent: Agent | null;
  step: string | undefined;
  onClose: () => void;
  onDone: (conversationId: string | null) => void;
}) {
  if (!mainAgent) return <NoSuchApp />;
  return (
    <PaneFault at="first-run">
      <Suspense fallback={<PaneLoading />}>
        <FirstRun
          agent={mainAgent}
          agents={agents}
          member={member}
          step={step}
          onStep={placeFirstRun}
          onClose={onClose}
          onDone={onDone}
        />
      </Suspense>
    </PaneFault>
  );
}
