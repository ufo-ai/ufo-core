import type { HTMLAttributes } from "react";

const NESTED_CONTROL = "button, a, input, select, textarea, label";

/** Makes a whole row the control that opens it — `role`/`tabIndex`, not a `<button>`, since a row's own
 *  controls cannot nest in one. `NESTED_CONTROL` keeps their presses; `keepsRole` spares a `<tr>`. */
export function rowControl(open: () => void, keepsRole = false): HTMLAttributes<HTMLElement> {
  return {
    ...(keepsRole ? {} : { role: "button" }),
    tabIndex: 0,
    className: "cursor-pointer",
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
