import { useCallback, useEffect, useState } from "react";
import {
  IconApps,
  IconBuildingStore,
  IconClockPlay,
  IconFile,
  IconMessage,
  IconPlug,
  IconPlus,
  IconRadar,
  IconSearch,
  IconUsers,
  type TablerIcon,
} from "@tabler/icons-react";

import { SidebarTooltip, type Chord } from "@/components/Sidebar";
import {
  Command,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandNote,
} from "@/components/ui/command";
import { Dialog, DialogContent, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { useViewer } from "@/lib/audience";
import { cn } from "@/lib/cn";
import {
  AGENTS_HASH,
  HOME_HASH,
  STORE_HASH,
  automationsHash,
  SECTIONS,
  newChatHash,
  sectionHash,
  workspaceHash,
  type Section,
  type WorkspaceTab,
} from "@/lib/route";
import { navigate } from "@/lib/router";
import { searchEverywhere, type Group } from "@/lib/search";
import { useOfferedTabs, useSurfaces } from "@/lib/surfaces";
import type { Agent, Surfaces } from "@/lib/types";
import { SECTION_VIEWS } from "@/views/registry";

/** A search that fired on every keystroke would run one fan-out per letter, and the member is still
 *  typing the word the last one answered. */
export const REST_MS = 200;

const BLANK = "Nothing matches this search.";

const WORKING = "Searching…";

/** Meta holds it alone: `ctrl+k` is kill-line in every readline-shaped field. */
const CHORD = "k";

const SEARCH = "Search";

const SEARCH_CHORD: Chord = { key: CHORD, cap: "\u2318K", aria: "Meta+K" };

const SECTION_ICONS: Partial<Record<Section, TablerIcon>> = {
  radar: IconRadar,
  artifacts: IconFile,
  connectors: IconPlug,
};

function places(
  landing: WorkspaceTab,
  surfaces: Surfaces,
): { label: string; hash: string; icon: TablerIcon }[] {
  return [
    { label: "Chat", hash: HOME_HASH, icon: IconMessage },
    { label: "Automations", hash: automationsHash(), icon: IconClockPlay },
    ...(surfaces.apps ? [{ label: "Apps", hash: AGENTS_HASH, icon: IconApps }] : []),
    ...(surfaces.apps && surfaces["app-store"]
      ? [{ label: "App Store", hash: STORE_HASH, icon: IconBuildingStore }]
      : []),
    ...SECTIONS.flatMap((section) => {
      const view = SECTION_VIEWS[section];
      const icon = SECTION_ICONS[section];
      if (!view || !icon) return [];
      if (section === "radar" && !surfaces.radar) return [];
      return [{ label: view.label, hash: sectionHash(section), icon }];
    }),
    { label: "Workspace", hash: workspaceHash(landing), icon: IconUsers },
  ].filter((row, at, rows) => rows.findIndex((other) => other.hash === row.hash) === at);
}

export function Spotlight({
  agents,
  className,
  collapsed,
  label,
}: {
  agents: Agent[];
  className?: string;
  collapsed?: boolean;
  label?: React.ReactNode;
}) {
  const tabs = useOfferedTabs();
  const surfaces = useSurfaces();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [settled, setSettled] = useState(false);
  const viewer = useViewer();
  const wanted = typed.trim();
  const named = agents.find((agent) => agent.main) ?? agents[0];

  const show = useCallback((next: boolean) => {
    setOpen(next);
    if (!next) setTyped("");
  }, []);

  useEffect(() => {
    const chord = (event: KeyboardEvent) => {
      if (event.key !== CHORD || event.altKey || event.ctrlKey || event.shiftKey) return;
      if (!event.metaKey) return;
      event.preventDefault();
      show(!open);
    };
    document.addEventListener("keydown", chord);
    return () => document.removeEventListener("keydown", chord);
  }, [open, show]);

  useEffect(() => {
    if (!open || !wanted) {
      setGroups(null);
      setSettled(false);
      return;
    }
    setSettled(false);
    const held = new AbortController();
    const timer = window.setTimeout(() => {
      /* Each kind is drawn as it lands rather than at the end of the fan-out: the reads are one per kind and
         the slowest would otherwise hold back every row the others answered. */
      searchEverywhere(wanted, agents, viewer, held.signal, (answering) => {
        if (!held.signal.aborted) setGroups(answering);
      })
        .then((found) => {
          if (held.signal.aborted) return;
          setGroups(found);
          setSettled(true);
        })
        /** Swallowing the fault leaves the box looking like a workspace holding nothing, which is the one
         *  answer it must never give by accident. */
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
  }, [open, wanted, agents, viewer]);

  const take = (hash: string) => {
    show(false);
    navigate(hash);
  };

  const started = named
    ? {
        value: "new-chat",
        primary: "New chat",
        icon: IconPlus,
        run: () => take(newChatHash(named.id)),
      }
    : null;
  const lowered = wanted.toLowerCase();
  const actions = [!wanted || "new chat".includes(lowered) ? started : null].filter(
    (action) => action !== null,
  );
  const reachable = places(tabs[0], surfaces).filter((place) =>
    place.label.toLowerCase().includes(lowered),
  );

  const status = !wanted ? null : !settled ? WORKING : groups?.length ? null : BLANK;
  return (
    <Dialog open={open} onOpenChange={show}>
      <SidebarTooltip collapsed={collapsed === true} label={SEARCH} chord={SEARCH_CHORD}>
        <DialogTrigger asChild>
          <button
            type="button"
            aria-label={SEARCH}
            aria-keyshortcuts={SEARCH_CHORD.aria}
            className={cn(className, open && "bg-fill")}
          >
            <IconSearch className="size-(--size-glyph) shrink-0" aria-hidden />
            {label}
          </button>
        </DialogTrigger>
      </SidebarTooltip>
      <DialogContent
        className="w-spotlight gap-0 overflow-y-hidden p-0"
        aria-describedby={undefined}
      >
        <DialogTitle className="sr-only">Search</DialogTitle>
        <Command label="Search" shouldFilter={false} loop>
          <CommandInput autoFocus placeholder="Search" value={typed} onValueChange={setTyped} />
          <CommandList label="Results">
            {actions.length ? (
              <CommandGroup heading="Actions">
                {actions.map((action) => (
                  <CommandItem
                    key={action.value}
                    value={action.value}
                    icon={action.icon}
                    primary={action.primary}
                    onSelect={action.run}
                  />
                ))}
              </CommandGroup>
            ) : null}
            {reachable.length ? (
              <CommandGroup heading="Places">
                {reachable.map((place) => (
                  <CommandItem
                    key={place.hash}
                    value={"place " + place.hash}
                    icon={place.icon}
                    primary={place.label}
                    onSelect={() => take(place.hash)}
                  />
                ))}
              </CommandGroup>
            ) : null}
            {(groups ?? []).map((group) => (
              <CommandGroup key={group.label} heading={group.label}>
                {group.failed ? <CommandNote>{group.failed}</CommandNote> : null}
                {group.hits.map((hit) => (
                  <CommandItem
                    key={hit.key}
                    value={group.label + " " + hit.key}
                    icon={group.icon}
                    primary={hit.primary}
                    fact={hit.fact}
                    onSelect={() => take(hit.hash)}
                  />
                ))}
              </CommandGroup>
            ))}
            {status ? <CommandNote>{status}</CommandNote> : null}
          </CommandList>
        </Command>
      </DialogContent>
    </Dialog>
  );
}

