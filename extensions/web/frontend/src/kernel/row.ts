import type { HTMLAttributes } from "react";

const NESTED_CONTROL = "button, a, input, select, textarea, label";

/** The attributes that make a record's whole row the control that opens it, spread onto whatever
 *  element the presentation makes a row out of — a `<tr>`, a row line's `<li>`, a card. The row
 *  is not a `<button>` because a row holds its own controls, and a button inside a button is not
 *  markup a browser will keep; `role` and `tabIndex` reach the same keyboard and the same
 *  announcement without nesting one interactive element in another.
 *
 *  `NESTED_CONTROL` is why: a press that lands on the row's own act — a `ConfirmButton`, a
 *  download link, a field — is that act's and not the row's, so opening never rides along with
 *  a delete. A row the member may not open is handed no control at all, so it takes no role, no
 *  tab stop, and no pointer cursor.
 *
 *  `keepsRole` is for an element that already carries one the reader navigates by: a `<tr>` is a
 *  row of a grid, and trading that for `button` takes it out of the table it belongs to. The tab
 *  stop and the keys are the same either way. */
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
