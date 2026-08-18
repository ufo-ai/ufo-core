import { useEffect, useState } from "react";
import { IconSearch } from "@tabler/icons-react";

import { Dialog, DialogContent, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { cn } from "@/lib/cn";
import { searchEverywhere, type Group } from "@/lib/search";
import type { Agent } from "@/lib/types";

/** How long a term rests before it is read. A search that fired on every keystroke would run one
 *  fan-out per letter, and the member is still typing the word the last one answered. */
const REST_MS = 200;

const BLANK = "Nothing matches this search.";

/** Search over the whole workspace, opened from the bar rather than standing in a column: one box
 *  and the hits under it, grouped by the kind that answered, so the reach is the same from every
 *  category. Picking a hit opens the place holding it and shuts the dialog. */
export function Spotlight({
  agents,
  className,
  onOpen,
}: {
  agents: Agent[];
  className?: string;
  onOpen: (hash: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [groups, setGroups] = useState<Group[] | null>(null);
  const wanted = typed.trim();

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
          setGroups([{ label: "Search", hits: [], failed: String(error) }]);
        });
    }, REST_MS);
    return () => {
      window.clearTimeout(timer);
      held.abort();
    };
  }, [open, wanted, agents]);

  const answered = groups !== null;
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setTyped("");
      }}
    >
      <DialogTrigger asChild>
        <button type="button" aria-label="Search" className={cn(className, open && "bg-fill")}>
          <IconSearch className="size-(--size-glyph)" aria-hidden />
        </button>
      </DialogTrigger>
      <DialogContent className="w-spotlight top-1/4 gap-2xl p-2xl" aria-describedby={undefined}>
        <DialogTitle className="sr-only">Search</DialogTitle>
        <input
          autoFocus
          type="search"
          aria-label="Search"
          placeholder="Search"
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
          className="w-full border-0 bg-transparent p-0 text-title text-inherit outline-none placeholder:text-ink-soft"
        />
        {answered && !groups.length ? (
          <p className="m-0 text-label text-ink-soft">{BLANK}</p>
        ) : null}
        {(groups ?? []).map((group) => (
          <section key={group.label} className="flex flex-col gap-2xs">
            <h3 className="m-0 text-label font-medium text-ink-soft">{group.label}</h3>
            {group.failed ? (
              <p className="m-0 px-sm text-label text-ink-soft">{group.failed}</p>
            ) : null}
            <ul className="m-0 flex list-none flex-col gap-px p-0">
              {group.hits.map((hit) => (
                <li key={hit.key}>
                  <button
                    type="button"
                    onClick={() => {
                      setOpen(false);
                      setTyped("");
                      onOpen(hit.hash);
                    }}
                    className="flex w-full items-baseline gap-sm rounded-control border-0 bg-transparent px-sm py-xs text-left text-label text-inherit hover:bg-fill"
                  >
                    <span className="min-w-0 truncate">{hit.primary}</span>
                    <span className="ml-auto shrink-0 truncate font-mono text-small text-ink-soft">
                      {hit.fact}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </DialogContent>
    </Dialog>
  );
}
