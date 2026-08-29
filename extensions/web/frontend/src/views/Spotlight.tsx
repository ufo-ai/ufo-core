import { useCallback, useEffect, useState } from "react";
import {
  IconApps,
  IconMessage,
  IconPlug,
  IconPlus,
  IconSearch,
  IconUsers,
  type TablerIcon,
} from "@tabler/icons-react";

import {
  Command,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandNote,
} from "@/components/ui/command";
import { Dialog, DialogContent, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { cn } from "@/lib/cn";
import { setPendingAsk } from "@/lib/pendingAsk";
import {
  AGENTS_HASH,
  HOME_HASH,
  SECTIONS,
  newChatHash,
  sectionHash,
  workspaceHash,
  type Section,
} from "@/lib/route";
import { navigate } from "@/lib/router";
import { searchEverywhere, type Group } from "@/lib/search";
import type { Agent } from "@/lib/types";
import { SECTION_VIEWS } from "@/views/registry";

/** How long a term rests before it is read. A search that fired on every keystroke would run one
 *  fan-out per letter, and the member is still typing the word the last one answered. */
const REST_MS = 200;

const BLANK = "Nothing matches this search.";

const WORKING = "Searching…";

/** The chord that opens the palette from anywhere, and closes it again. Meta holds it alone:
 *  `ctrl+k` is kill-line in every readline-shaped field, so it is not a chord to take away. */
export const CHORD = "k";

const SECTION_ICONS: Partial<Record<Section, TablerIcon>> = {
  connectors: IconPlug,
};

/** Where the bar reaches, in the order it lists them. These rows are the same destinations as the
 *  buttons beside the search glyph — the palette adds no place the nav does not already carry —
 *  and each takes the glyph its kind is drawn with wherever a hit of that kind stands. App-shipped
 *  screens stand in the palette as the apps themselves, so only the portal's own sections list. */
const PLACES: { label: string; hash: string; icon: TablerIcon }[] = [
  { label: "Home", hash: HOME_HASH, icon: IconMessage },
  { label: "Apps", hash: AGENTS_HASH, icon: IconApps },
  ...SECTIONS.flatMap((section) => {
    const view = SECTION_VIEWS[section];
    const icon = SECTION_ICONS[section];
    if (!view || !icon) return [];
    return [{ label: view.label, hash: sectionHash(section), icon }];
  }),
  { label: "Workspace", hash: workspaceHash("team"), icon: IconUsers },
];

/** Search over the whole workspace, opened from the bar or by `⌘K`: one box, and under it what the
 *  member can do with the term, where they can go, and what the workspace holds — grouped by the
 *  kind that answered, so the reach is the same from every category. Arrow keys move the cursor
 *  and Enter takes the row under it; picking a hit opens the place holding it and shuts the dialog.
 *
 *  Only the acts a member can express as a route or a message stand here: the palette lands them in
 *  a chat with their words already in the composer, and the sending is theirs.
 *
 *  Every row is an address and the router writes it. The bar stands in the sidebar and again on the
 *  phone bar, and a hit taken in either one moves the page by the same act. */
export function Spotlight({
  agents,
  className,
  label,
}: {
  agents: Agent[];
  className?: string;
  label?: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [groups, setGroups] = useState<Group[] | null>(null);
  const wanted = typed.trim();
  /** The agent a workspace-owned act is expressed to — the main one, as the artifacts screen does. */
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
      return;
    }
    const held = new AbortController();
    const timer = window.setTimeout(() => {
      searchEverywhere(wanted, agents, held.signal)
        .then((found) => {
          if (!held.signal.aborted) setGroups(found);
        })
        /** A search that broke states it. Swallowing the fault leaves the box looking like a
         *  workspace holding nothing, which is the one answer it must never give by accident. */
        .catch((error: unknown) => {
          if (held.signal.aborted) return;
          setGroups([{ label: "Search", icon: IconSearch, hits: [], failed: String(error) }]);
        });
    }, REST_MS);
    return () => {
      window.clearTimeout(timer);
      held.abort();
    };
  }, [open, wanted, agents]);

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
  /** The term as something to say rather than something to find: the agent it is said to, then
   *  the words themselves, the way a chat row names its own subject. It lands in the composer of a
   *  new chat, so the member reads what will be sent and sends it themselves. */
  const asked =
    named && wanted
      ? {
          value: "ask",
          primary: named.name + ": " + wanted,
          icon: IconMessage,
          run: () => {
            setPendingAsk(named.id, wanted, true, "new:" + named.id);
            take(newChatHash(named.id));
          },
        }
      : null;
  const lowered = wanted.toLowerCase();
  const actions = [asked, !wanted || "new chat".includes(lowered) ? started : null].filter(
    (action) => action !== null,
  );
  const places = PLACES.filter((place) => place.label.toLowerCase().includes(lowered));

  const answered = groups !== null;
  const status = !wanted ? null : !answered ? WORKING : groups.length ? null : BLANK;
  return (
    <Dialog open={open} onOpenChange={show}>
      <DialogTrigger asChild>
        <button
          type="button"
          aria-label="Search"
          aria-keyshortcuts="Meta+K"
          className={cn(className, open && "bg-fill")}
        >
          <IconSearch className="size-(--size-glyph) shrink-0" aria-hidden />
          {label}
        </button>
      </DialogTrigger>
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
            {places.length ? (
              <CommandGroup heading="Places">
                {places.map((place) => (
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
