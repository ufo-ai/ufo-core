import { useEffect, useState } from "react";
import {
  IconClipboardCheck,
  IconDots,
  IconHistory,
  IconLayoutSidebarRight,
  IconMessageDots,
  IconPlug,
  IconPlus,
  IconSettings,
} from "@tabler/icons-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { PressRow } from "@/components/ui/pressrow";
import { Header } from "@/kernel/pane";
import { Panel, PanelEmpty, usePanelRead } from "@/kernel/panel";
import { useSlot } from "@/kernel/slots";
import { isPortalChat, subject, surfaceWord, useViewer } from "@/lib/audience";
import { AudienceMark } from "@/lib/audienceMark";
import { agentName } from "@/lib/agentName";
import { BUILD_ASK, setPendingAsk } from "@/lib/pendingAsk";
import { CHAT_SURFACE, useAgents } from "@/lib/mainAgent";
import { cn } from "@/lib/cn";
import type { ChatRow } from "@/lib/rail";
import type { SetupState } from "@/views/AgentSetup";
import { Chat } from "@/views/Chat";
import { ConversationDetail, Disclose, conversationTitle } from "@/views/Conversations";
import {
  COMPOSE,
  agentSetupHash,
  mergePlace,
  type PlaceStep,
  type WorkspacePlace,
} from "@/lib/route";
import { navigate } from "@/lib/router";
import { HomepageFrame, useHomepage } from "@/views/HomepageFrame";
import type { Agent, Conversation, Member } from "@/lib/types";
import { GLYPH_STROKE } from "@/lib/glyph";

const NEW_CONVERSATION = "New conversation";
const NEW = "New";
const BUILD_PAGE = "Build page";

const FRESH = "new";

const HISTORY = "History";
const NO_HISTORY = "No conversations yet.";
const HISTORY_BOUND = "The newest few. Search finds an older one.";

export const SETTINGS_TABS = ["settings", "connectors", "scheduled"] as const;
export type SettingsTab = (typeof SETTINGS_TABS)[number];
export const SETTINGS_TAB_LABELS: Record<SettingsTab, string> = {
  settings: "Settings",
  connectors: "Connectors",
  scheduled: "Scheduled",
};


const SETTINGS_TAB_GLYPHS: Record<SettingsTab, typeof IconSettings> = {
  settings: IconSettings,
  connectors: IconPlug,
  scheduled: IconClipboardCheck,
};

export function SettingsTabItems({ onPick }: { onPick: (tab: SettingsTab) => void }) {
  return (
    <>
      {SETTINGS_TABS.map((tab) => {
        const Glyph = SETTINGS_TAB_GLYPHS[tab];
        return (
          <DropdownMenuItem
            key={tab}
            className="justify-start gap-md rounded-row text-label tracking-ui"
            onSelect={() => onPick(tab)}
          >
            <Glyph className="size-(--size-glyph) shrink-0" stroke={GLYPH_STROKE} aria-hidden />
            {SETTINGS_TAB_LABELS[tab]}
          </DropdownMenuItem>
        );
      })}
    </>
  );
}

function AppMenu({
  name,
  onPick,
  className,
}: {
  name: string;
  onPick: (tab: SettingsTab) => void;
  className?: string;
}) {
  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <Button variant="quiet" size="icon" aria-label={"Menu for " + name} className={className}>
          <IconDots aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-(--container-menu)">
        <SettingsTabItems onPick={onPick} />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export type AgentPaneProps = {
  agent: Agent;
  member: Member;
  /** Null is not the empty list: a conversation the member owns would otherwise read as one they may not
   *  continue for as long as the rail takes to arrive. */
  chats: ChatRow[] | null;
  place: WorkspacePlace;
  onSettings: (tab: SettingsTab) => void;
  onCreated: (conversationId: string, title: string) => void;
  onFounded: (agent: Agent, conversationId: string, title: string) => void;
  onAgents: () => void;
  onPlace: (place: WorkspacePlace, step: PlaceStep) => void;
};

export function AgentPane({
  agent,
  member,
  chats,
  place,
  onSettings,
  onCreated,
  onFounded,
  onAgents,
  onPlace,
}: AgentPaneProps) {
  const [settles, setSettles] = useState(0);
  const viewer = useViewer();
  const agents = useAgents();
  const target = place.opens?.[0];
  const home = useHomepage(agent, settles);
  const speaks = agent.app === CHAT_SURFACE && home.state === "set";
  // The move replaces rather than pushes: a pushed entry sends Back to the app address, which mounts the
  // pane, reads the same answer and pushes the setup screen over it again.
  const flagged = agent.stands_on_setup === true;
  const setup = usePanelRead<SetupState>(
    flagged ? "/agents/" + agent.id + "/setup" : null,
    settles,
  );
  const unbuilt = flagged && setup.phase === "ready" && setup.payload.own_page === false;
  const built = flagged && setup.phase === "ready" && setup.payload.own_page === true;
  useEffect(() => {
    if (unbuilt) navigate(agentSetupHash(agent.id), "replace");
  }, [unbuilt, agent.id]);
  useEffect(() => {
    if (built) onAgents();
  }, [built, onAgents]);
  const listed = usePanelRead<{ conversations: Conversation[]; more: boolean }>(
    !speaks && target !== COMPOSE && (target !== undefined || home.state !== "set")
      ? "/agents/" + agent.id + "/conversations"
      : null,
    settles,
  );
  const rows = listed.phase === "ready" ? (listed.payload.conversations ?? []) : [];
  const directives =
    chats === null
      ? null
      : chats.filter((row) => row.agent_id === agent.id && row.mine && isPortalChat(row.surface));
  const live = directives === null ? null : new Set(directives.map((row) => row.conversation_id));
  const editing =
    directives === null
      ? null
      : directives.reduce<ChatRow | null>(
          (newest, row) => (newest === null || row.last_at > newest.last_at ? row : newest),
          null,
        );
  const composeFallback = target === COMPOSE && home.state !== "set";
  // The two reads are bounded differently — the index answers one page of 100 rows over every surface
  // the app has spoken on, while the rail bounds the member's own chats — so a row can fall outside it.
  const railHeld =
    target !== undefined && !rows.some((entry) => entry.id === target)
      ? (directives?.find((row) => row.conversation_id === target) ?? null)
      : null;
  const conversational =
    !speaks &&
    (composeFallback ||
      (target !== undefined &&
        (target === FRESH || rows.some((entry) => entry.id === target) || railHeld !== null)));
  const held = composeFallback ? FRESH : conversational ? target : undefined;
  const wanted = held === FRESH;
  const named =
    held !== undefined && !wanted ? (rows.find((entry) => entry.id === held) ?? null) : null;
  // The index answers `readable` from audience membership alone, so a conversation this member has just
  // opened still arrives false, and every press would write another audit row for a disclosure made.
  const [disclosed, setDisclosed] = useState<string | null>(null);
  const start = () => onPlace({ ...place, opens: [FRESH] }, "push");
  const buildPage = () => {
    setPendingAsk(agent.id, BUILD_ASK, false);
    onPlace({ ...place, opens: [FRESH] }, "push");
  };
  const [listing, setListing] = useState<string | null>(null);
  const lane = held ?? FRESH;
  const history = held !== undefined && listing === lane;
  const opened =
    wanted || railHeld !== null
      ? null
      : (named ??
        (live === null
          ? null
          : (rows.find((entry) => entry.id === editing?.conversation_id) ??
            rows.find((entry) => live.has(entry.id)) ??
            null)));
  const settling =
    opened === null &&
    !wanted &&
    railHeld === null &&
    (live === null || listed.phase === "loading");
  const walled = opened !== null && !opened.readable && !opened.disclosable;
  const gated = opened !== null && !walled && !opened.readable && disclosed !== opened.id;
  const reading =
    opened !== null &&
    !walled &&
    !gated &&
    live !== null &&
    !live.has(opened.id) &&
    !opened.commentable;

  const url = home.state === "set" ? home.url : null;
  const generation = home.state === "set" ? (home.deploy_generation ?? 0) : 0;
  const beside = url !== null;
  const railRow =
    target !== undefined && !conversational && !beside
      ? ((chats ?? []).find(
          (row) => row.conversation_id === target && row.mine && isPortalChat(row.surface),
        ) ?? null)
      : null;
  const railAgent = railRow ? (agents.find((entry) => entry.id === railRow.agent_id) ?? null) : null;
  const missing =
    target !== undefined &&
    !conversational &&
    !beside &&
    railAgent === null &&
    listed.phase === "ready";
  const framed = mergePlace(place, conversational ? { opens: undefined } : {});
  const audience = opened ?? railHeld;

  const conversation = (
    <section aria-label={agentName(agent.name)} className="flex min-h-0 min-w-0 flex-1 flex-col">
      {beside ? null : (
        <Header
          heading={2}
          title={
            opened
              ? opened.commentable
                ? conversationTitle(opened, viewer)
                : subject(opened, viewer)
              : (railHeld?.title ?? NEW_CONVERSATION)
          }
          note={audience ? <AudienceMark entry={audience} /> : null}
          acts={
            <>
              {beside ? null : (
                <Button variant="quiet" size="bar" onClick={buildPage}>
                  {BUILD_PAGE}
                </Button>
              )}
              <Button variant="send" size="bar" onClick={start}>
                {NEW}
              </Button>
              <AppMenu name={agentName(agent.name)} onPick={onSettings} />
            </>
          }
          pinned
        />
      )}
      {listed.phase === "failed" ? (
        <PanelEmpty>{listed.message}</PanelEmpty>
      ) : missing ? (
        <PanelEmpty>This conversation is not in {agentName(agent.name)}.</PanelEmpty>
      ) : settling ? null : walled ? (
        <PanelEmpty>This conversation is not shared with this account.</PanelEmpty>
      ) : gated ? (
        <div className="min-h-0 flex-1 overflow-y-auto scrollbar-gutter-stable p-2xl">
          <Disclose
            key={opened.id}
            agent={agent}
            conversation={opened}
            onOpened={() => setDisclosed(opened.id)}
          />
        </div>
      ) : reading ? (
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
          key={opened?.id ?? railHeld?.conversation_id ?? "new"}
          agent={agent}
          member={member}
          conversationId={opened?.id ?? railHeld?.conversation_id ?? null}
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

  const past = (
    <div className="min-h-0 flex-1 overflow-y-auto scrollbar-gutter-stable py-md">
      <Panel
        state={listed}
        shape="table"
        empty={(payload) => ((payload.conversations ?? []).length ? null : NO_HISTORY)}
      >
        {(payload) => (
          <div className="flex flex-col">
            {(payload.conversations ?? []).map((row) => (
              <PressRow
                key={row.id}
                line={subject(row, viewer)}
                note={isPortalChat(row.surface) ? undefined : surfaceWord(row.surface)}
                when={row.last_turn_at ?? undefined}
                onPress={() => onPlace({ ...place, opens: [row.id] }, "push")}
              />
            ))}
            {payload.more ? (
              <p className="m-0 px-lg py-lg text-label text-ink-soft">{HISTORY_BOUND}</p>
            ) : null}
          </div>
        )}
      </Panel>
    </div>
  );

  const slot = useSlot(beside && held !== undefined ? (history ? past : conversation) : null, {
    id: lane,
    kind: "panel",
    title: history
      ? HISTORY
      : opened
        ? subject(opened, viewer)
        : (railHeld?.title ?? NEW_CONVERSATION),
    note: history || !audience ? null : <AudienceMark entry={audience} />,
    acts: (
      <>
        <Button
          variant="mark"
          size="glyph"
          aria-label={NEW_CONVERSATION + " with " + agentName(agent.name)}
          disabled={held === FRESH}
          onClick={start}
        >
          <IconPlus stroke={GLYPH_STROKE} aria-hidden />
        </Button>
        <Button
          variant="mark"
          size="glyph"
          aria-label={HISTORY + " for " + agentName(agent.name)}
          aria-pressed={history}
          className={cn(history && "text-ink")}
          onClick={() => setListing(history ? null : lane)}
        >
          <IconHistory stroke={GLYPH_STROKE} aria-hidden />
        </Button>
      </>
    ),
    onClose: history ? undefined : () => onPlace({ ...place, opens: [] }, "replace"),
    onBack: history ? () => setListing(null) : undefined,
  });

  if (beside) {
    return (
      <>
        <section
          aria-label={agentName(agent.name) + " homepage"}
          className="relative flex min-h-0 min-w-0 flex-1 flex-col"
        >
          {speaks ? null : (
            <div className="absolute top-lg right-2xl z-10 flex items-center gap-xs">
              <AppMenu
                name={agentName(agent.name)}
                onPick={onSettings}
                className="rounded-full border border-edge bg-surface"
              />
              <Button
                variant="quiet"
                size="icon"
                aria-label={
                  (held !== undefined ? "Close edit of " : "Edit ") + agentName(agent.name)
                }
                aria-pressed={held !== undefined}
                className={cn(
                  "rounded-full border border-edge bg-surface",
                  held !== undefined && "bg-fill",
                )}
                onClick={() =>
                  held === undefined
                    ? onPlace(
                        { ...place, opens: [opened?.id ?? editing?.conversation_id ?? FRESH] },
                        "push",
                      )
                    : onPlace({ ...place, opens: [] }, "replace")
                }
              >
                {held !== undefined ? (
                  <IconLayoutSidebarRight aria-hidden />
                ) : (
                  <IconMessageDots aria-hidden />
                )}
              </Button>
            </div>
          )}
          <HomepageFrame
            agent={agent}
            member={member}
            url={url}
            generation={generation}
            place={framed}
            banded={false}
            onFounded={(speaking, conversationId, title) => {
              onFounded(speaking, conversationId, title);
              setSettles((count) => count + 1);
            }}
          />
        </section>
        {slot}
      </>
    );
  }

  if (railRow && railAgent && target !== undefined) {
    return (
      <section
        aria-label={railRow.title}
        className="flex min-h-0 min-w-0 flex-1 flex-col"
      >
        <Header
          heading={2}
          title={railRow.title}
          note={<AudienceMark entry={railRow} />}
          pinned
        />
        <Chat
          key={target}
          agent={railAgent}
          member={member}
          conversationId={target}
          onCreated={onCreated}
          onSettled={() => setSettles((count) => count + 1)}
        />
      </section>
    );
  }
  return (
    <>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">{conversation}</div>
    </>
  );
}
