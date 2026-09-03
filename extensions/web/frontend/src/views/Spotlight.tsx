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
import { useOfferedTabs } from "@/lib/surfaces";
import { TRACK_MAX_SLOTS, heldTrack } from "@/lib/tracks";
import type { Agent } from "@/lib/types";
import { SECTION_VIEWS } from "@/views/registry";

/** How long a term rests before it is read. A search that fired on every keystroke would run one
 *  fan-out per letter, and the member is still typing the word the last one answered. */
const REST_MS = 200;

const BLANK = "Nothing matches this search.";

const WORKING = "Searching…";

/** The chord that opens the palette from anywhere, and walks its pages once it stands. Meta holds
 *  it alone: `ctrl+k` is kill-line in every readline-shaped field, so it is not a chord to take
 *  away. */
export const CHORD = "k";

/** The key that says the term out loud rather than looking it up. It is the completion key every
 *  launcher spends this way, and the box keeps it: nothing in the panel is reached by tabbing. */
export const ASK_KEY = "Tab";

const COMMAND = "⌘";

const ENTER = "↵";

/** The rows the meta digits reach, in the order they stand on the screen. */
const ROW_KEYS = "123456789";

const BOX = "[cmdk-input]";

const ITEM = "[cmdk-item]";

/** How many conversations the root lists: enough to hold what a member is working through, short
 *  enough that the apps and the places above them stay on the screen. */
const THREADS_SHOWN = 12;

const APPLICATIONS = "Applications";
const THREADS = "Threads";
const RESULTS = "Results";
const RESULT_THREADS = "Result threads";
const ACTIONS = "Actions";
const PLACES = "Places";

const LAUNCHER = "Launcher";

const ASKING = "What are you looking for?";

const ASK = "Ask";

const OPEN = "Open";

/** The way out of the twelve conversations the root lists and into all of them: the same scope the
 *  main app's row opens, which is where a conversation is searched for by name. */
const HISTORY = "See more history";

const FILTER = "Filter threads";

/** The apps group the workspace read answers with. The palette lists every app from the roster the
 *  shell already holds, so the read's own app hits would draw each of them a second time. */
const SEARCHED_APPS = "Apps";

/** The conversations group the workspace read answers with. A thread is a lane wherever it is
 *  opened from, so these rows are drawn and opened as the rail's own threads are rather than as
 *  addresses the pane is replaced by. */
const SEARCHED_THREADS = "Conversations";

const SECTION_ICONS: Partial<Record<Section, TablerIcon>> = {
  connectors: IconPlug,
};

/** The glyph each surface a conversation arrives on is drawn with, the marks the rest of the portal
 *  draws them under. */
const SURFACE_ICONS: Partial<Record<string, TablerIcon>> = {
  [SLACK_SURFACE]: IconBrandSlack,
  [UFO_SURFACE]: IconTerminal2,
  [IMESSAGE_SURFACE]: IconMessageCircle,
};

type Audience = "all" | "mine" | "shared";

/** Whose conversations a scope lists. A member reads their own by default and the workspace's
 *  beside them, so the pick narrows rather than widens. */
const AUDIENCES: { value: Audience; label: string }[] = [
  { value: "all", label: "All" },
  { value: "mine", label: "Mine" },
  { value: "shared", label: "Shared" },
];

/** Where the palette stands: over everything the workspace holds, over the scopes a search can be
 *  narrowed to, or inside one of those scopes reading its conversations. */
type Page = { kind: "root" } | { kind: "scopes" } | { kind: "threads"; scope: Scope };

/** The glyph a row is drawn with: a tabler mark, or an app's own mark closed over the app. */
type Mark = (props: { className?: string; "aria-hidden"?: boolean }) => ReactNode;

type Row = {
  /** What cmdk knows the row by, unique across every group the page draws: a value held twice is
   *  one row to the cursor. */
  value: string;
  /** The mark the row stands under. A conversation takes none: its title is the whole row, and a
   *  column of the same glyph beside a column of titles states nothing the titles do not. */
  icon?: Mark;
  primary: string;
  secondary?: string;
  fact?: string;
  /** Whether the conversation is the member's own, where that is known: the rail's fact for a row
   *  it carries, else whose the read said the conversation is. A row stating nothing is let through
   *  by every filter. */
  mine?: boolean;
  /** What Enter does. A row naming something the member talks in — an app, a thread — enters it as
   *  a lane at the near end of home, the way the rail's own tiles open one. A place is a screen and
   *  not a lane — home, the apps listing, connectors, the workspace — so its row is the plain route
   *  and the pane the member is standing in is replaced by it. */
  open: () => void;
  /** What Enter does on this row, where that is something other than opening what it names. The
   *  foot draws it, so the key is read against the row the cursor is on rather than the panel. */
  hint?: string;
  /** Where the row stands when the member asks for it beside what they already have open. A row
   *  that names no lane — a place, an act — offers none, and the gesture opens it as Enter does. */
  aside?: () => void;
};

type Run = { heading: string; action?: ReactNode; rows: Row[]; note?: string };

/** Where the bar reaches, in the order it lists them. These rows are the same destinations as the
 *  buttons beside the search glyph — the palette adds no place the nav does not already carry —
 *  and each takes the glyph its kind is drawn with wherever a hit of that kind stands. App-shipped
 *  screens stand in the palette as the apps themselves, so only the portal's own sections list.
 *  The workspace row opens the destination's first offered tab, the same one its nav row does — and
 *  where that address is one already listed, the palette carries it once rather than under two
 *  names. */
function places(landing: WorkspaceTab): { label: string; hash: string; icon: TablerIcon }[] {
  return [
    { label: "Home", hash: HOME_HASH, icon: IconMessage },
    { label: "Apps", hash: AGENTS_HASH, icon: IconApps },
    ...SECTIONS.flatMap((section) => {
      const view = SECTION_VIEWS[section];
      const icon = SECTION_ICONS[section];
      if (!view || !icon) return [];
      return [{ label: view.label, hash: sectionHash(section), icon }];
    }),
    { label: "Workspace", hash: workspaceHash(landing), icon: IconUsers },
  ].filter((row, at, rows) => rows.findIndex((other) => other.hash === row.hash) === at);
}

/** Home holding one more lane, entered at the near end where the member is looking: under the cap
 *  the row slides along to make room, and at the cap the lane at the far end falls off, the row
 *  ageing away from the member. A lane the row already holds is one lane — a conversation names the
 *  same lane wherever it is opened from — so the row comes back as it stands and the seek that
 *  follows carries the member to the lane that is already there. */
function entered(track: string[], lane: string): string[] {
  if (track.includes(lane)) return track;
  return [lane, ...(track.length < TRACK_MAX_SLOTS ? track : track.slice(0, -1))];
}

/** The mark the rail draws home with, in the column a glyph stands in. The palette reaches
 *  everything the workspace holds, which is what that tile opens, so the foot names the root page
 *  under the same shape. */
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

/** The mark the sidebar draws an app with, as a row's glyph: the same component, closed over the
 *  app it names, so a row and the tile the member knows the app by are the one shape. */
function agentMark(agent: Agent): Mark {
  return ({ className }) => <AgentIcon name={agent.icon} className={className} />;
}

/** What a scope is called wherever it is named: the app's own name, or the member's word for the
 *  surface the conversations arrived on. */
function scopeLabel(scope: Scope): string {
  return scope.kind === "app" ? agentName(scope.agent.name) : scope.label;
}

function inScope(row: ChatRow, scope: Scope): boolean {
  return scope.kind === "app" ? row.agent_id === scope.agent.id : row.surface === scope.surface;
}

/** Search over the whole workspace, opened from the bar or by `⌘K`: one box, and under it what the
 *  member can do with the term, where they can go, which apps they have, what they were last
 *  talking about, and what the workspace holds — grouped by the kind that answered. Arrow keys move
 *  the cursor, Enter takes the row under it, `⌘↵` stands it beside what home already holds, and
 *  `⌘1`…`⌘9` take the row at that place in the list.
 *
 *  `⌘K` again narrows the search to one app or one surface, and the box then reads that scope's own
 *  conversations; Escape and Backspace step back out of it before they shut the panel.
 *
 *  Only the acts a member can express as a route or a message stand here: an app, a thread and a
 *  term alike land in a lane of their own at the near end of home, said by the composer standing in
 *  it, and every one of them enters under the same seek a press on the rail's own tiles runs.
 *
 *  Every row is an address and the router writes it. The bar stands in the sidebar and again on the
 *  phone bar, and a hit taken in either one moves the page by the same act. */
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
  /** The member's own conversations as the rail carries them, newest first. */
  chats: ChatRow[];
  /** The apps the member pinned, which is the order the palette lists every app in. */
  pinned: string[];
  /** Stand home on `opens` and bring `lane` into view as it lands. The shell owns the seek — the
   *  arrival, the mark the rail draws on the lane, the expanded row it ends — so a lane opened from
   *  here lands exactly as one opened from the column this panel stands in. */
  onEnterLane: (lane: string, opens: string[]) => void;
  className?: string;
  label?: ReactNode;
  /** The mark the opening button draws, and what it is called: the search glyph where a bar holds
   *  it, the plus where the rail leads with it. */
  glyph?: TablerIcon;
  title?: string;
}) {
  const tabs = useOfferedTabs();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [page, setPage] = useState<Page>({ kind: "root" });
  const [groups, setGroups] = useState<Group[] | null>(null);
  /** Whether the workspace read has finished. The kinds land one at a time and the panel draws each
   *  as it arrives, so what has already landed does not say the search is over. */
  const [settled, setSettled] = useState(false);
  const [found, setFound] = useState<Group | null>(null);
  const [audience, setAudience] = useState<Audience>("all");
  const viewer = useViewer();
  /** The row the cursor stands on, as cmdk knows it. The foot names the key that row answers to,
   *  so the panel has to hold the selection rather than leave it inside the list. */
  const [selected, setSelected] = useState("");
  /** Whether the press taking a row asked for it beside what the member already has open. cmdk
   *  hands a selection to the row without the event that made it, so the gesture is read off the
   *  keydown that precedes it. */
  const wantsBeside = useRef(false);
  const wanted = typed.trim();
  const lowered = wanted.toLowerCase();
  /** The agent a workspace-owned act is expressed to — the main one, as the artifacts screen does. */
  const named = agents.find((agent) => agent.main) ?? agents[0];
  /** The act the box's own key runs, under the name the app is drawn by everywhere else. */
  const asked = named ? ASK + " " + agentName(named.name) : "";

  /** Back out to the whole workspace, the box cleared: the term that found a scope was the way into
   *  it, not a search the member is still running inside it. */
  const toRoot = useCallback(() => {
    setPage({ kind: "root" });
    setTyped("");
    setAudience("all");
  }, []);

  /** Stand the box inside one scope, reading its conversations. The term that reached the scope was
   *  the way in rather than a search inside it, so the box starts empty there. */
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

  /** Where a row that names a screen lands: the address itself, replacing the pane the member is
   *  standing in. A place is not a lane, and neither is a record another screen holds. */
  const take = useCallback(
    (hash: string) => {
      show(false);
      navigate(hash);
    },
    [show],
  );

  /** Where every row that names something the member talks in lands: one lane at the near end of
   *  home, entering as a press on the rail does — the panel shuts, home stands the row with the
   *  lane at position 0, and the seek brings it in. A lane home is already holding is not opened a
   *  second time; the seek carries the member to the one standing. */
  const enterLane = useCallback(
    (lane: string) => {
      show(false);
      onEnterLane(lane, entered(heldTrack("home"), lane));
    },
    [show, onEnterLane],
  );

  /** Where a row stands when the member asks for it beside what they are already reading: the far
   *  end of home, nothing they were holding shut. A row already standing, and a row a full row of
   *  lanes has no room for, leave the lanes as they are — and the seek lands the member on the lane
   *  they asked for either way. */
  const besideLane = useCallback(
    (lane: string) => {
      show(false);
      onEnterLane(lane, appended(heldTrack("home"), lane));
    },
    [show, onEnterLane],
  );

  /** A fresh chat with an app: a lane of its own at the near end of home, where the member is
   *  looking, and nothing they were holding shut. The lane it lands in is the key the words are
   *  left under, so a term said from here is said by that lane's own composer. */
  const toChat = useCallback(
    (agentId: string, said?: string) => {
      const lane = mintHomeLane(agentId, heldTrack("home"));
      if (said) setPendingAsk(agentId, said, true, lane);
      enterLane(lane);
    },
    [enterLane],
  );

  /** The term as something to say rather than something to find. */
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
      /* A field the member is typing in that is not the palette's own box keeps its own keys: the
         chord above is the one thing that reaches past whatever holds the cursor. */
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
    /* Read before cmdk and before the dialog: the palette answers Enter, Tab and the digits itself,
       and a handler running after them would be answering keys already spent. */
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
      /* Each kind is drawn as it lands rather than at the end of the fan-out: the reads are one per
         kind and the slowest of them would otherwise hold back every row the others answered. */
      searchEverywhere(wanted, agents, held.signal, (answering) => {
        if (!held.signal.aborted) setGroups(answering);
      })
        .then((answered) => {
          if (held.signal.aborted) return;
          setGroups(answered);
          setSettled(true);
        })
        /** A search that broke states it. Swallowing the fault leaves the box looking like a
         *  workspace holding nothing, which is the one answer it must never give by accident. */
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
  }, [open, page.kind, wanted, agents]);

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

  /** A conversation as a row, wherever one is listed: the rail's own rows, a scope's, and what the
   *  workspace read answered with. Enter enters its lane at the near end of home and `⌘↵` stands it
   *  at the far end, so a thread taken from here joins the lanes the member is holding rather than
   *  standing a screen over them. The value is the caller's: one conversation can stand in two
   *  groups of one page, and a value held twice is one row to the cursor. */
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

  /** A conversation the rail carries, as a row. */
  const railRow = (row: ChatRow): Row =>
    threadRow({
      value: "thread " + row.conversation_id,
      conversation: row.conversation_id,
      primary: row.title || agentName(row.agent_name),
      mine: row.mine,
    });

  /** One app or one surface as a row that narrows the box to it. It states the search it stands
   *  for rather than the thing it names, so the word `threads` reaches every one of them. */
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
    /* What a search found is a conversation the rail may also carry. Whose it is is the rail's
       fact where it carries the row, and the read's own — the owner it answered — for every hit
       outside the rail's bound, so the filter narrows the whole run rather than the part of it the
       rail happens to hold. */
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
      rows: places(tabs[0])
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
    const threadsRun: Run = {
      heading: THREADS,
      rows:
        named && listedThreads.length
          ? [
              ...listedThreads,
              {
                value: "history",
                icon: IconArrowRight,
                primary: HISTORY,
                open: () => toScope({ kind: "app", agent: named }),
              },
            ]
          : listedThreads,
    };
    /** What the workspace answered, kind by kind, under the rows the shell could list on its own.
     *  The apps it names are the roster this palette already draws, so that one group is dropped
     *  rather than standing every app a second time. */
    const readRuns: Run[] = (groups ?? [])
      .filter((group) => group.label !== SEARCHED_APPS)
      .map((group) => ({
        heading: group.label,
        rows: group.hits.map((hit) =>
          group.label === SEARCHED_THREADS
            ? threadRow({
                value: group.label + " " + hit.key,
                conversation: hit.key,
                primary: hit.primary,
                fact: hit.fact,
              })
            : {
                value: group.label + " " + hit.key,
                icon: group.icon,
                primary: hit.primary,
                fact: hit.fact,
                open: () => take(hit.hash),
              },
        ),
        note: group.failed ?? undefined,
      }));
    /** A term is answered acts first — the member typed words to do something with them — and an
     *  empty box is answered with the work itself: what is running, what they have, where they
     *  were. */
    const ordered: Run[] = wanted
      ? [
          actionsRun,
          placesRun,
          {
            heading: RESULTS,
            rows: scopes.map(scopeRow).filter((row) => row.primary.toLowerCase().includes(lowered)),
          },
          appsRun,
          threadsRun,
          ...readRuns,
        ]
      : [appsRun, threadsRun, actionsRun, placesRun];
    runs.push(...ordered.filter((run) => run.rows.length > 0 || run.note !== undefined));
  }

  /** How many kinds the read has answered with, and nothing before the first of them lands. The
   *  scopes page narrows rows that are already drawn and reads nothing, so it says nothing
   *  either. */
  const answered = page.kind === "threads" ? found?.hits.length : groups?.length;
  /** Whether the read is still running. A root read is over when its last kind lands, not when the
   *  first one does, so the line stands under the groups already drawn. */
  const working = page.kind === "root" ? !settled : found === null;
  /** The row the cursor stands on, and the row it falls to. cmdk holds the cursor by value, and a
   *  value from the page before this one names no row here: the cursor would then stand on nothing,
   *  Enter would answer nothing, and an arrow press would be what put it back. */
  const first = runs[0]?.rows[0]?.value ?? "";
  const under = runs.flatMap((run) => run.rows).find((row) => row.value === selected);
  useEffect(() => {
    setSelected(first);
  }, [page, first]);
  const status =
    !wanted || page.kind === "scopes" ? null : working ? WORKING : answered ? null : BLANK;
  /** What the foot calls the page the member is standing on. */
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
        /* Escape leaves a scope before it leaves the palette: the member narrowed the search in one
           press, and one press is what widens it again. */
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
