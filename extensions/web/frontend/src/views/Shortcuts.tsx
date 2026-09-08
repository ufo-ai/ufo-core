import { useEffect, useState } from "react";

import { CommandKbd } from "@/components/ui/command";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { CLAIMED, DIALOG, LANE_NEXT, LANE_PRIOR, TYPING } from "@/kernel/slots";
import { ASK_KEY, CHORD as SEARCH } from "@/views/Spotlight";

const CHORD = "?";

const COMMAND = "⌘";

const LEAVE_FIELD = "Esc";

const ENTER = "↵";

const ROW_RANGE = "1…9";

const TITLE = "Keyboard shortcuts";

const SHORTCUTS: { group: string; rows: { act: string; keys: string[] }[] }[] = [
  {
    group: "Navigation",
    rows: [
      { act: "Search", keys: [COMMAND, SEARCH.toUpperCase()] },
      { act: TITLE, keys: [CHORD] },
    ],
  },
  {
    group: "Launcher",
    rows: [
      { act: "Open", keys: [ENTER] },
      { act: "Open beside", keys: [COMMAND, ENTER] },
      { act: "Ask", keys: [ASK_KEY] },
      { act: "Back", keys: [LEAVE_FIELD] },
      { act: "Row 1–9", keys: [COMMAND, ROW_RANGE] },
      { act: "Next page", keys: [COMMAND, SEARCH.toUpperCase()] },
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
                      <CommandKbd key={key}>{key}</CommandKbd>
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
