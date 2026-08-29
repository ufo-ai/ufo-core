import { useEffect, useState } from "react";

import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { CLAIMED, DIALOG, LANE_NEXT, LANE_PRIOR, TYPING } from "@/kernel/slots";
import { CHORD as SEARCH } from "@/views/Spotlight";

/** The key that opens this list, pressed bare, as Linear spends it — so a member arrives already
 *  holding it. Shift is what makes the character, so shift is not a modifier the guard refuses. */
const CHORD = "?";

/** The command key as a Mac keyboard draws it. The palette answers Meta alone, so a keyboard
 *  without one reaches the palette by its bar rather than by a second chord this could name. */
const COMMAND = "⌘";

const LEAVE_FIELD = "Esc";

const TITLE = "Keyboard shortcuts";

/** Where `?` is a character the member is typing rather than a chord: the composer, a search box, a
 *  name field. Focus standing in one of these is what tells the two apart. */

/** Every chord the portal answers. Each key is read from the module that answers it, so what this
 *  prints cannot drift from what the keyboard does. */
const SHORTCUTS: { group: string; rows: { act: string; keys: string[] }[] }[] = [
  {
    group: "Navigation",
    rows: [
      { act: "Search", keys: [COMMAND, SEARCH.toUpperCase()] },
      { act: TITLE, keys: [CHORD] },
    ],
  },
  {
    group: "Lanes",
    rows: [
      { act: "Previous lane", keys: [LANE_PRIOR] },
      { act: "Next lane", keys: [LANE_NEXT] },
      { act: "Leave the message box", keys: [LEAVE_FIELD] },
    ],
  },
];

/** The list of chords, opened by `?` from anywhere and shut by `?` again or by Escape. It draws
 *  nothing until it is opened, so it stands beside the shell rather than inside a screen. */
export function Shortcuts() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const chord = (event: KeyboardEvent) => {
      if (event.key !== CHORD || event.defaultPrevented) return;
      if (event.altKey || event.metaKey || event.ctrlKey) return;
      const active = document.activeElement;
      if (!open) {
        if (active instanceof HTMLElement && active.closest(CLAIMED)) return;
        if (document.querySelector(DIALOG)) return;
        if (active instanceof HTMLElement && (active.closest(TYPING) || active.isContentEditable)) {
          return;
        }
      }
      event.preventDefault();
      setOpen((shown) => !shown);
    };
    document.addEventListener("keydown", chord);
    return () => document.removeEventListener("keydown", chord);
  }, [open]);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="gap-2xl" aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>{TITLE}</DialogTitle>
        </DialogHeader>
        {SHORTCUTS.map((section) => (
          <section key={section.group} className="flex flex-col gap-2xs">
            <h3 className="m-0 text-small font-strong text-ink-soft">{section.group}</h3>
            <dl className="m-0 flex flex-col">
              {section.rows.map((row) => (
                <div
                  key={row.act}
                  data-slot="shortcut"
                  className="flex items-center justify-between gap-lg py-xs"
                >
                  <dt className="text-ui">{row.act}</dt>
                  <dd className="m-0 flex items-center gap-2xs">
                    {row.keys.map((key) => (
                      <kbd
                        key={key}
                        className="inline-flex min-w-2xl items-center justify-center rounded-control border border-edge bg-fill px-2xs py-hair font-mono text-mono text-ink"
                      >
                        {key}
                      </kbd>
                    ))}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </DialogContent>
    </Dialog>
  );
}
