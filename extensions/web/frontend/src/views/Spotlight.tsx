import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  IconApps,
  IconArrowRight,
  IconBrandSlack,
  IconFilter2,
  IconMessage,
  IconMessageCircle,
  IconPlug,
  IconSearch,
  IconTerminal2,
  IconUsers,
  type TablerIcon,
} from "@tabler/icons-react";

import { Button } from "@/components/ui/button";
import {
  Command,
  CommandFoot,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandKbd,
  CommandList,
  CommandNote,
} from "@/components/ui/command";
import { Dialog, DialogContent, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { TYPING, appended, beside } from "@/kernel/slots";
import { AgentIcon } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";
import { IMESSAGE_SURFACE, SLACK_SURFACE, UFO_SURFACE, useViewer } from "@/lib/audience";
import { cn } from "@/lib/cn";
import { GLYPH_STROKE } from "@/lib/glyph";
import { setPendingAsk } from "@/lib/pendingAsk";
import { CHAT_SHOWN_OPTIONS, appOrder, type ChatRow } from "@/lib/rail";
import {
  AGENTS_HASH,
  HOME_HASH,
  SECTIONS,
  homeConversationLane,
  mintHomeLane,
  sectionHash,
  workspaceHash,
  type Section,
  type WorkspaceTab,
} from "@/lib/route";
import { navigate } from "@/lib/router";
import { searchEverywhere, searchThreads, type Group, type Scope } from "@/lib/search";
import { useOfferedTabs, useSurfaces } from "@/lib/surfaces";
import { TRACK_MAX_SLOTS, heldTrack } from "@/lib/tracks";
import type { Agent } from "@/lib/types";
import { SECTION_VIEWS } from "@/views/registry";

export const REST_MS = 200;

const BLANK = "Nothing matches this search.";

const WORKING = "Searching…";

export const CHORD = "k";

export const ASK_KEY = "Tab";

const COMMAND = "⌘";

const ENTER = "↵";

const ROW_KEYS = "123456789";

const BOX = "[cmdk-input]";

const ITEM = "[cmdk-item]";

const THREADS_SHOWN = 12;

const APPLICATIONS = "Applications";
const THREADS = "Threads";
const RESULT_THREADS = "Result threads";
const ACTIONS = "Actions";
const PLACES = "Places";

const LAUNCHER = "Launcher";

const ASKING = "What are you looking for?";

const ASK = "Ask";

const OPEN = "Open";

const HISTORY = "See more history";

const FILTER = "Filter threads";

const SEARCHED_APPS = "Apps";

const SEARCHED_THREADS = THREADS;

const SECTION_ICONS: Partial<Record<Section, TablerIcon>> = {
  connectors: IconPlug,
};

const SURFACE_ICONS: Partial<Record<string, TablerIcon>> = {
  [SLACK_SURFACE]: IconBrandSlack,
  [UFO_SURFACE]: IconTerminal2,
  [IMESSAGE_SURFACE]: IconMessageCircle,
};

type Audience = "all" | "mine" | "shared";

const AUDIENCES: { value: Audience; label: string }[] = [
  { value: "all", label: "All" },
  { value: "mine", label: "Mine" },
  { value: "shared", label: "Shared" },
];

type Page = { kind: "root" } | { kind: "scopes" } | { kind: "threads"; scope: Scope };

type Mark = (props: { className?: string; "aria-hidden"?: boolean }) => ReactNode;

type Row = {
  value: string;
  icon?: Mark;
  primary: string;
  secondary?: string;
  fact?: string;
  mine?: boolean;
  open: () => void;
  hint?: string;
  aside?: () => void;
};

type Run = { heading: string; action?: ReactNode; rows: Row[]; note?: string };

function places(
  landing: WorkspaceTab,
  apps: boolean,
): { label: string; hash: string; icon: TablerIcon }[] {
  return [
    { label: "Home", hash: HOME_HASH, icon: IconMessage },
    ...(apps ? [{ label: "Apps", hash: AGENTS_HASH, icon: IconApps }] : []),
    ...SECTIONS.flatMap((section) => {
      const view = SECTION_VIEWS[section];
      const icon = SECTION_ICONS[section];
      if (!view || !icon) return [];
      return [{ label: view.label, hash: sectionHash(section), icon }];
    }),
    { label: "Workspace", hash: workspaceHash(landing), icon: IconUsers },
  ].filter((row, at, rows) => rows.findIndex((other) => other.hash === row.hash) === at);
}

function entered(track: string[], lane: string): string[] {
  if (track.includes(lane)) return track;
  return [lane, ...(track.length < TRACK_MAX_SLOTS ? track : track.slice(0, -1))];
}

function HomeMark() {
  return (
    <span className="flex size-(--size-glyph) shrink-0 items-center justify-center">
      <svg width={12.5} height={11} viewBox="0 0 12.5 11" fill="currentColor" aria-hidden>
        <circle cx={2} cy={2} r={2} />
        <circle cx={10.5} cy={2} r={2} />
        <circle cx={6.25} cy={9} r={2} />
      </svg>
    </span>
  );
}

function agentMark(agent: Agent): Mark {
  return ({ className }) => <AgentIcon name={agent.icon} className={className} />;
}

function scopeLabel(scope: Scope): string {
  return scope.kind === "app" ? agentName(scope.agent.name) : scope.label;
}

function inScope(row: ChatRow, scope: Scope): boolean {
  return scope.kind === "app" ? row.agent_id === scope.agent.id : row.surface === scope.surface;
}

export function Spotlight({
  agents,
  chats,
  pinned,
  onEnterLane,
  className,
  label,
  glyph: Glyph = IconSearch,
  title = "Search",
}: {
  agents: Agent[];
  chats: ChatRow[];
  pinned: string[];
  onEnterLane: (lane: string, opens: string[]) => void;
  className?: string;
  label?: ReactNode;
  glyph?: TablerIcon;
  title?: string;
}) {
  const tabs = useOfferedTabs();
  const surfaces = useSurfaces();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [page, setPage] = useState<Page>({ kind: "root" });
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [settled, setSettled] = useState(false);
  const [found, setFound] = useState<Group | null>(null);
  const [audience, setAudience] = useState<Audience>("all");
  const viewer = useViewer();
  const [selected, setSelected] = useState("");
  const wantsBeside = useRef(false);
  const wanted = typed.trim();
  const lowered = wanted.toLowerCase();
  const named = agents.find((agent) => agent.main) ?? agents[0];
  const asked = named ? ASK + " " + agentName(named.name) : "";

  const toRoot = useCallback(() => {
    setPage({ kind: "root" });
    setTyped("");
    setAudience("all");
  }, []);

  const toScope = useCallback((scope: Scope) => {
    setTyped("");
    setPage({ kind: "threads", scope });
  }, []);

  const show = useCallback(
    (next: boolean) => {
      setOpen(next);
      if (!next) toRoot();
    },
    [toRoot],
  );

  const take = useCallback(
    (hash: string) => {
      show(false);
      navigate(hash);
    },
    [show],
  );

  const enterLane = useCallback(
    (lane: string) => {
      show(false);
      onEnterLane(lane, entered(heldTrack("home"), lane));
    },
    [show, onEnterLane],
  );

  const besideLane = useCallback(
    (lane: string) => {
      show(false);
      onEnterLane(lane, appended(heldTrack("home"), lane));
    },
    [show, onEnterLane],
  );

  const toChat = useCallback(
    (agentId: string, said?: string) => {
      const lane = mintHomeLane(agentId, heldTrack("home"));
      if (said) setPendingAsk(agentId, said, true, lane);
      enterLane(lane);
    },
    [enterLane],
  );

  const ask = useCallback(() => {
    if (!named || !wanted) return;
    toChat(named.id, wanted);
  }, [named, wanted, toChat]);

  useEffect(() => {
    const pressed = (event: KeyboardEvent) => {
      if (
        event.key === CHORD &&
        event.metaKey &&
        !event.altKey &&
        !event.ctrlKey &&
        !event.shiftKey
      ) {
        event.preventDefault();
        if (!open) {
          setOpen(true);
          return;
        }
        if (page.kind === "threads") toRoot();
        else setPage(page.kind === "root" ? { kind: "scopes" } : { kind: "root" });
        return;
      }
      if (!open) return;
      const cursor = document.activeElement;
      if (
        cursor instanceof HTMLElement &&
        !cursor.matches(BOX) &&
        (cursor.closest(TYPING) || cursor.isContentEditable)
      ) {
        return;
      }
      if (event.key === "Enter") {
        wantsBeside.current = beside(event);
        return;
      }
      if (event.key === ASK_KEY) {
        event.preventDefault();
        ask();
        return;
      }
      if (event.metaKey && ROW_KEYS.includes(event.key)) {
        event.preventDefault();
        document.querySelectorAll<HTMLElement>(ITEM)[Number(event.key) - 1]?.click();
        return;
      }
      if (page.kind !== "threads" || typed) return;
      if (event.key === "Backspace" || event.key === "ArrowLeft") {
        event.preventDefault();
        toRoot();
      }
    };
    document.addEventListener("keydown", pressed, true);
    return () => document.removeEventListener("keydown", pressed, true);
  }, [open, page, typed, ask, toRoot]);

  useEffect(() => {
    if (!open || page.kind !== "root" || !wanted) {
      setGroups(null);
      setSettled(false);
      return;
    }
    setSettled(false);
    const held = new AbortController();
    const timer = window.setTimeout(() => {
      searchEverywhere(wanted, agents, viewer, held.signal, (answering) => {
        if (!held.signal.aborted) setGroups(answering);
      })
        .then((answered) => {
          if (held.signal.aborted) return;
          setGroups(answered);
          setSettled(true);
        })
        .catch((error: unknown) => {
          if (held.signal.aborted) return;
          setGroups([{ label: "Search", icon: IconSearch, hits: [], failed: String(error) }]);
          setSettled(true);
        });
    }, REST_MS);
    return () => {
      window.clearTimeout(timer);
      held.abort();
    };
  }, [open, page.kind, wanted, agents, viewer]);

  useEffect(() => {
    if (!open || page.kind !== "threads" || !wanted) {
      setFound(null);
      return;
    }
    const scope = page.scope;
    const held = new AbortController();
    const timer = window.setTimeout(() => {
      searchThreads(scope, wanted, agents, viewer, held.signal)
        .then((answered) => {
          if (!held.signal.aborted) setFound(answered);
        })
        .catch((error: unknown) => {
          if (held.signal.aborted) return;
          setFound({ label: RESULT_THREADS, icon: IconMessage, hits: [], failed: String(error) });
        });
    }, REST_MS);
    return () => {
      window.clearTimeout(timer);
      held.abort();
    };
  }, [open, page, wanted, agents, viewer]);

  const threadRow = (row: {
    value: string;
    conversation: string;
    primary: string;
    mine?: boolean;
    fact?: string;
  }): Row => ({
    value: row.value,
    primary: row.primary,
    mine: row.mine,
    fact: row.fact,
    open: () => enterLane(homeConversationLane(row.conversation)),
    aside: () => besideLane(homeConversationLane(row.conversation)),
  });

  const railRow = (row: ChatRow): Row =>
    threadRow({
      value: "thread " + row.conversation_id,
      conversation: row.conversation_id,
      primary: row.title || agentName(row.agent_name),
      mine: row.mine,
      fact:
        row.title && row.agent_name && row.agent_id !== named?.id
          ? agentName(row.agent_name)
          : undefined,
    });

  const scopeRow = (scope: Scope): Row => ({
    value: "scope " + (scope.kind === "app" ? scope.agent.id : scope.surface),
    icon:
      scope.kind === "app"
        ? agentMark(scope.agent)
        : (SURFACE_ICONS[scope.surface] ?? IconMessage),
    primary: "Search " + scopeLabel(scope) + " threads",
    fact: scope.kind === "app" ? "Application" : "Connection",
    open: () => toScope(scope),
  });

  const scopes: Scope[] = [
    ...appOrder(agents, pinned).map((agent) => ({ kind: "app" as const, agent })),
    ...CHAT_SHOWN_OPTIONS.map((option) => ({
      kind: "surface" as const,
      surface: option.surface,
      label: option.label,
    })),
  ];
  const runs: Run[] = [];
  if (page.kind === "threads") {
    const carried = new Map(chats.map((row) => [row.conversation_id, row]));
    const rows = wanted
      ? (found?.hits ?? []).map((hit) =>
          threadRow({
            value: "thread " + hit.key,
            conversation: hit.key,
            primary: hit.primary,
            mine: carried.get(hit.key)?.mine ?? hit.mine,
          }),
        )
      : chats.filter((row) => inScope(row, page.scope)).map(railRow);
    runs.push({
      heading: RESULT_THREADS,
      action: (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="mark"
              size="glyph"
              aria-label={FILTER}
              className={cn(audience !== "all" && "text-ink")}
            >
              <IconFilter2 aria-hidden stroke={GLYPH_STROKE} />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuRadioGroup
              value={audience}
              onValueChange={(next) => setAudience(next as Audience)}
            >
              {AUDIENCES.map((entry) => (
                <DropdownMenuRadioItem key={entry.value} value={entry.value}>
                  {entry.label}
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      ),
      rows: rows.filter(
        (row) => audience === "all" || row.mine === undefined || (audience === "mine") === row.mine,
      ),
      note: found?.failed ?? undefined,
    });
  } else if (page.kind === "scopes") {
    runs.push({
      heading: THREADS,
      rows: scopes.map(scopeRow).filter((row) => row.primary.toLowerCase().includes(lowered)),
    });
  } else {
    const actionsRun: Run = {
      heading: ACTIONS,
      rows:
        named && wanted
          ? [{ value: "ask", primary: asked + ": " + wanted, hint: asked, open: ask }]
          : [],
    };
    const placesRun: Run = {
      heading: PLACES,
      rows: places(tabs[0], surfaces.apps)
        .filter((place) => place.label.toLowerCase().includes(lowered))
        .map((place) => ({
          value: "place " + place.hash,
          icon: place.icon,
          primary: place.label,
          open: () => take(place.hash),
        })),
    };
    const appsRun: Run = {
      heading: APPLICATIONS,
      rows: appOrder(agents, pinned)
        .filter((agent) => agent.name.toLowerCase().includes(lowered))
        .map((agent) => ({
          value: "app " + agent.id,
          icon: agentMark(agent),
          primary: agentName(agent.name),
          secondary: agent.purpose ?? undefined,
          open: () => toChat(agent.id),
          aside: () => besideLane(mintHomeLane(agent.id, heldTrack("home"))),
        })),
    };
    const listedThreads = chats
      .filter((row) => row.title.toLowerCase().includes(lowered))
      .slice(0, THREADS_SHOWN)
      .map(railRow);
    const searched = (groups ?? []).find((group) => group.label === SEARCHED_THREADS);
    const searchedThreads = (searched?.hits ?? [])
      .filter((hit) => !listedThreads.some((row) => row.value === "thread " + hit.key))
      .map((hit) =>
        threadRow({
          value: "thread " + hit.key,
          conversation: hit.key,
          primary: hit.primary,
          fact: hit.fact,
        }),
      );
    const threadsRun: Run = {
      heading: THREADS,
      rows:
        named && listedThreads.length
          ? [
              ...listedThreads,
              ...searchedThreads,
              {
                value: "history",
                icon: IconArrowRight,
                primary: HISTORY,
                open: () => toScope({ kind: "app", agent: named }),
              },
            ]
          : [...listedThreads, ...searchedThreads],
      note: searched?.failed ?? undefined,
    };
    const readRuns: Run[] = (groups ?? [])
      .filter((group) => group.label !== SEARCHED_APPS && group.label !== SEARCHED_THREADS)
      .map((group) => ({
        heading: group.label,
        rows: group.hits.map((hit) => ({
          value: group.label + " " + hit.key,
          icon: group.icon,
          primary: hit.primary,
          fact: hit.fact,
          open: () => take(hit.hash),
        })),
        note: group.failed ?? undefined,
      }));
    const ordered: Run[] = wanted
      ? [actionsRun, placesRun, appsRun, threadsRun, ...readRuns]
      : [appsRun, threadsRun, actionsRun, placesRun];
    runs.push(...ordered.filter((run) => run.rows.length > 0 || run.note !== undefined));
  }

  const answered = page.kind === "threads" ? found?.hits.length : groups?.length;
  const working = page.kind === "root" ? !settled : found === null;
  const first = runs[0]?.rows[0]?.value ?? "";
  const under = runs.flatMap((run) => run.rows).find((row) => row.value === selected);
  useEffect(() => {
    setSelected(first);
  }, [page, first]);
  const status =
    !wanted || page.kind === "scopes" ? null : working ? WORKING : answered ? null : BLANK;
  const standing =
    page.kind === "root"
      ? LAUNCHER
      : page.kind === "scopes"
        ? THREADS
        : scopeLabel(page.scope) + " threads";
  return (
    <Dialog open={open} onOpenChange={show}>
      <DialogTrigger asChild>
        <button
          type="button"
          aria-label={title}
          aria-keyshortcuts="Meta+K"
          className={cn(className, open && "bg-fill")}
        >
          <Glyph className="size-(--size-glyph) shrink-0" aria-hidden />
          {label}
        </button>
      </DialogTrigger>
      <DialogContent
        className="w-spotlight gap-0 overflow-y-hidden p-0"
        aria-describedby={undefined}
        onEscapeKeyDown={(event) => {
          if (page.kind !== "threads") return;
          event.preventDefault();
          toRoot();
        }}
      >
        <DialogTitle className="sr-only">Search</DialogTitle>
        <Command
          label="Search"
          shouldFilter={false}
          loop
          value={selected}
          onValueChange={setSelected}
        >
          <CommandInput
            autoFocus
            placeholder={
              page.kind === "threads" ? "Search " + scopeLabel(page.scope) + " threads" : ASKING
            }
            value={typed}
            onValueChange={setTyped}
            onBack={page.kind === "threads" ? toRoot : undefined}
            trailing={
              named ? (
                <span className="flex shrink-0 items-center gap-sm text-small text-ink-soft">
                  {asked}
                  <CommandKbd>{ASK_KEY}</CommandKbd>
                </span>
              ) : undefined
            }
          />
          <CommandList label="Results">
            {runs.map((run) => (
              <CommandGroup key={run.heading} heading={run.heading} action={run.action}>
                {run.note ? <CommandNote>{run.note}</CommandNote> : null}
                {run.rows.map((row) => (
                  <CommandItem
                    key={row.value}
                    value={row.value}
                    icon={row.icon}
                    primary={row.primary}
                    secondary={row.secondary}
                    fact={row.fact}
                    onSelect={() => {
                      const wants = wantsBeside.current;
                      wantsBeside.current = false;
                      if (wants && row.aside) row.aside();
                      else row.open();
                    }}
                  />
                ))}
              </CommandGroup>
            ))}
            {status ? <CommandNote>{status}</CommandNote> : null}
          </CommandList>
          <CommandFoot
            lead={
              <>
                {page.kind === "root" ? (
                  <HomeMark />
                ) : (
                  <IconSearch className="size-(--size-glyph) shrink-0" aria-hidden />
                )}
                {standing}
              </>
            }
            hints={[
              ...(under ? [{ label: under.hint ?? OPEN, keys: [ENTER] }] : []),
              { label: ACTIONS, keys: [COMMAND + CHORD.toUpperCase()] },
            ]}
          />
        </Command>
      </DialogContent>
    </Dialog>
  );
}
