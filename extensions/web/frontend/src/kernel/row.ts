import type { HTMLAttributes } from "react";

/* A menu's items are not buttons, and its content sits inside the row it was opened from, so a pick
   would otherwise fire the row's own press behind the menu. */
const NESTED_CONTROL =
  "button, a, input, select, textarea, label, [role=menuitem], [role=menu], [data-slot=dropdown-menu-content]";

/** Makes a whole row the control that opens it — `role`/`tabIndex`, not a `<button>`, since a row's own
 *  controls cannot nest in one. `NESTED_CONTROL` keeps their presses; `keepsRole` spares a `<tr>`. */
export function rowControl(open: () => void, keepsRole = false): HTMLAttributes<HTMLElement> {
  return {
    ...(keepsRole ? {} : { role: "button" }),
    tabIndex: 0,
    className: "cursor-pointer focus-visible:-outline-offset-2 focus-visible:outline-2 focus-visible:outline-ring",
    onClick: (event) => {
      if ((event.target as HTMLElement).closest(NESTED_CONTROL)) return;
      open();
    },
    onKeyDown: (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      if ((event.target as HTMLElement).closest(NESTED_CONTROL)) return;
      event.preventDefault();
      open();
    },
  };
}
